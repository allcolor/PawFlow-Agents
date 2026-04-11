"""Tests for the deployment registry."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.deployment_registry import (
    DeployedInstance,
    DeploymentRegistry,
    DEPLOYMENTS_DIR,
    GLOBAL_OWNER,
)


class TestDeployedInstance(unittest.TestCase):
    """Tests for the DeployedInstance dataclass."""

    def test_to_dict(self):
        inst = DeployedInstance(
            instance_id="test_001",
            flow_id="test",
            flow_name="Test Flow",
            flow_path="flows/test.json",
        )
        d = inst.to_dict()
        assert d["instance_id"] == "test_001"
        assert d["flow_id"] == "test"
        assert "owner" not in d  # None values stripped

    def test_to_dict_with_owner(self):
        inst = DeployedInstance(
            instance_id="test_001",
            flow_id="test",
            flow_name="Test Flow",
            flow_path="flows/test.json",
            owner="alice",
        )
        d = inst.to_dict()
        assert d["owner"] == "alice"

    def test_from_dict(self):
        data = {
            "instance_id": "test_001",
            "flow_id": "test",
            "flow_name": "Test Flow",
            "flow_path": "flows/test.json",
            "owner": "alice",
            "status": "running",
        }
        inst = DeployedInstance.from_dict(data)
        assert inst.instance_id == "test_001"
        assert inst.owner == "alice"
        assert inst.status == "running"

    def test_from_dict_ignores_unknown(self):
        data = {
            "instance_id": "test_001",
            "flow_id": "test",
            "flow_name": "Test Flow",
            "flow_path": "flows/test.json",
            "unknown_field": "ignored",
        }
        inst = DeployedInstance.from_dict(data)
        assert inst.instance_id == "test_001"
        assert not hasattr(inst, "unknown_field")

    def test_roundtrip(self):
        inst = DeployedInstance(
            instance_id="test_001",
            flow_id="test",
            flow_name="Test Flow",
            flow_path="flows/test.json",
            owner="bob",
            status="running",
            parameters={"greeting": "Hello"},
        )
        d = inst.to_dict()
        inst2 = DeployedInstance.from_dict(d)
        assert inst2.instance_id == inst.instance_id
        assert inst2.owner == inst.owner
        assert inst2.parameters == inst.parameters


class TestDeploymentRegistry(unittest.TestCase):
    """Tests for the DeploymentRegistry singleton."""

    def setUp(self):
        DeploymentRegistry.reset()
        # Use a temp dir for deployments
        self._orig_dir = DEPLOYMENTS_DIR
        self._tmp = Path(tempfile.mkdtemp())
        self._dep_dir = self._tmp / "deployments"
        self._dep_dir.mkdir()

        # Create a test template
        self._flows_dir = self._tmp / "flows"
        self._flows_dir.mkdir()
        self._template = self._flows_dir / "test-flow.json"
        self._template.write_text(json.dumps({
            "id": "test-flow",
            "name": "Test Flow",
            "version": "1.0.0",
            "tasks": {"gen": {"type": "generateFlowFile", "parameters": {}}},
            "relations": [],
        }), encoding="utf-8")

        # Monkey-patch the module-level DEPLOYMENTS_DIR
        import core.deployment_registry as mod
        self._mod = mod
        mod.DEPLOYMENTS_DIR = self._dep_dir

    def tearDown(self):
        DeploymentRegistry.reset()
        self._mod.DEPLOYMENTS_DIR = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _get_reg(self):
        reg = DeploymentRegistry.get_instance()
        reg._loaded = True  # Skip disk scan
        return reg

    def test_singleton(self):
        r1 = DeploymentRegistry.get_instance()
        r2 = DeploymentRegistry.get_instance()
        assert r1 is r2

    def test_reset(self):
        r1 = DeploymentRegistry.get_instance()
        DeploymentRegistry.reset()
        r2 = DeploymentRegistry.get_instance()
        assert r1 is not r2

    def test_deploy_creates_instance(self):
        reg = self._get_reg()
        iid = reg.deploy(
            template_path=str(self._template),
            owner="alice",
            parameters={"greeting": "Hello"},
        )
        assert iid.startswith("test-flow__")
        inst = reg.get(iid)
        assert inst is not None
        assert inst.flow_id == "test-flow"
        assert inst.flow_name == "Test Flow"
        assert inst.owner == "alice"
        assert inst.status == "stopped"
        assert inst.parameters == {"greeting": "Hello"}

    def test_deploy_same_template_twice(self):
        reg = self._get_reg()
        id1 = reg.deploy(template_path=str(self._template), owner="alice")
        id2 = reg.deploy(template_path=str(self._template), owner="alice")
        assert id1 != id2
        assert len(reg.get_all()) == 2

    def test_deploy_file_not_found(self):
        reg = self._get_reg()
        with self.assertRaises(FileNotFoundError):
            reg.deploy(template_path="nonexistent.json")

    def test_deploy_with_explicit_instance_id(self):
        reg = self._get_reg()
        iid = reg.deploy(
            template_path=str(self._template),
            instance_id="my-custom-id",
        )
        assert iid == "my-custom-id"

    def test_undeploy_removes_instance(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template))
        assert reg.get(iid) is not None
        reg.undeploy(iid)
        assert reg.get(iid) is None

    def test_undeploy_nonexistent(self):
        reg = self._get_reg()
        reg.undeploy("nonexistent")  # Should not raise

    def test_update_status(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template))
        assert reg.get(iid).status == "stopped"

        reg.update_status(iid, "running")
        assert reg.get(iid).status == "running"
        assert reg.get(iid).last_started is not None

        reg.update_status(iid, "stopped")
        assert reg.get(iid).status == "stopped"
        assert reg.get(iid).last_stopped is not None

    def test_update_status_with_error(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template))
        reg.update_status(iid, "error", "something broke")
        inst = reg.get(iid)
        assert inst.status == "error"
        assert inst.error_message == "something broke"

    def test_update_status_nonexistent(self):
        reg = self._get_reg()
        reg.update_status("nonexistent", "running")  # Should not raise

    def test_set_owner(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template), owner="alice")
        assert reg.get(iid).owner == "alice"

        reg.set_owner(iid, "bob")
        assert reg.get(iid).owner == "bob"

    def test_set_owner_to_global(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template), owner="alice")
        reg.set_owner(iid, None)
        assert reg.get(iid).owner is None

    def test_get_all(self):
        reg = self._get_reg()
        reg.deploy(template_path=str(self._template), owner="alice")
        reg.deploy(template_path=str(self._template), owner="bob")
        assert len(reg.get_all()) == 2

    def test_get_grouped(self):
        reg = self._get_reg()
        reg.deploy(template_path=str(self._template), owner=None)
        reg.deploy(template_path=str(self._template), owner="alice")
        reg.deploy(template_path=str(self._template), owner="alice")

        grouped = reg.get_grouped()
        assert GLOBAL_OWNER in grouped
        assert len(grouped[GLOBAL_OWNER]) == 1
        assert "alice" in grouped
        assert len(grouped["alice"]) == 2

    def test_get_by_owner(self):
        reg = self._get_reg()
        reg.deploy(template_path=str(self._template), owner="alice")
        reg.deploy(template_path=str(self._template), owner="bob")
        reg.deploy(template_path=str(self._template), owner="alice")

        alice = reg.get_by_owner("alice")
        assert len(alice) == 2
        bob = reg.get_by_owner("bob")
        assert len(bob) == 1

    def test_get_by_conversation(self):
        reg = self._get_reg()
        reg.deploy(template_path=str(self._template), owner="alice",
                   conversation_id="conv1")
        reg.deploy(template_path=str(self._template), owner="alice",
                   conversation_id="conv2")
        reg.deploy(template_path=str(self._template), owner="bob",
                   conversation_id="conv1")

        results = reg.get_by_conversation("conv1", owner="alice")
        assert len(results) == 1
        results_all = reg.get_by_conversation("conv1")
        assert len(results_all) == 2

    def test_persistence_save_and_scan(self):
        """Test that instances saved to disk can be loaded back."""
        reg = self._get_reg()
        iid = reg.deploy(
            template_path=str(self._template),
            owner="alice",
            parameters={"key": "value"},
        )

        # Verify file exists on disk
        file_path = self._dep_dir / "alice" / f"{iid}.json"
        assert file_path.exists()

        # Reset and scan from disk
        DeploymentRegistry.reset()
        reg2 = DeploymentRegistry.get_instance()
        reg2._scan_disk()
        inst = reg2.get(iid)
        assert inst is not None
        assert inst.owner == "alice"
        assert inst.parameters == {"key": "value"}

    def test_persistence_global_owner(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template), owner=None)

        file_path = self._dep_dir / "global" / f"{iid}.json"
        assert file_path.exists()

    def test_delete_instance_file(self):
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template), owner="alice")

        file_path = self._dep_dir / "alice" / f"{iid}.json"
        assert file_path.exists()

        reg.undeploy(iid)
        assert not file_path.exists()

    def test_sync_with_executors_dead_instance(self):
        """Running instance whose executor died → marked stopped."""
        reg = self._get_reg()
        iid = reg.deploy(template_path=str(self._template))
        reg.update_status(iid, "running")

        # Mock executor registry with no executors
        with patch("core.executor_registry.ExecutorRegistry") as MockER:
            mock_reg = MagicMock()
            mock_reg.get_all.return_value = {}
            MockER.get_instance.return_value = mock_reg

            reg.sync_with_executors()

        assert reg.get(iid).status == "stopped"

    def test_thread_safety(self):
        """Concurrent deploys don't corrupt state."""
        reg = self._get_reg()
        results = []

        def deploy_fn(n):
            try:
                iid = reg.deploy(
                    template_path=str(self._template),
                    owner=f"user_{n}",
                )
                results.append(iid)
            except Exception as e:
                results.append(f"ERROR: {e}")

        threads = [threading.Thread(target=deploy_fn, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(not r.startswith("ERROR") for r in results)
        assert len(reg.get_all()) == 10

