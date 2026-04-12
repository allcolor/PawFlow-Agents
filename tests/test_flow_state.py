"""Tests for FlowStateManager and FlowVersionStore."""

import json
import os
import pytest

from engine.flow_state import FlowStateManager, FlowStateEntry


# ---------------------------------------------------------------------------
# FlowStateManager tests
# ---------------------------------------------------------------------------

class TestFlowStateManager:

    def test_register_and_save(self, tmp_path):
        state_file = str(tmp_path / "running.json")
        mgr = FlowStateManager(state_file)
        mgr.register_flow("flow1", parameters={"key": "val"})
        assert os.path.exists(state_file)

        # Reload
        mgr2 = FlowStateManager(state_file)
        mgr2.load()
        entries = mgr2.get_all_entries()
        assert len(entries) == 1
        assert entries[0].flow_id == "flow1"
        assert entries[0].parameters == {"key": "val"}
        assert entries[0].status == "running"

    def test_unregister(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.register_flow("flow1")
        mgr.register_flow("flow2")
        mgr.unregister_flow("flow1")
        assert len(mgr.get_all_entries()) == 1
        assert mgr.get_all_entries()[0].flow_id == "flow2"

    def test_mark_crashed(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.register_flow("flow1")
        mgr.mark_crashed("flow1", "Server killed")
        entry = mgr.get_entry("flow1")
        assert entry.status == "crashed"
        assert entry.error == "Server killed"

    def test_mark_recovery_failed(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.register_flow("flow1")
        mgr.mark_recovery_failed("flow1", "Config not found")
        entry = mgr.get_entry("flow1")
        assert entry.status == "recovery_failed"

    def test_mark_recovered(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.register_flow("flow1")
        mgr.mark_crashed("flow1")
        mgr.mark_recovered("flow1")
        entry = mgr.get_entry("flow1")
        assert entry.status == "running"
        assert entry.error == ""

    def test_get_flows_to_recover(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.register_flow("flow1")
        mgr.register_flow("flow2")
        mgr.mark_crashed("flow2")

        to_recover = mgr.get_flows_to_recover()
        assert len(to_recover) == 1
        assert to_recover[0].flow_id == "flow1"

    def test_persistence(self, tmp_path):
        state_file = str(tmp_path / "running.json")
        mgr1 = FlowStateManager(state_file)
        mgr1.register_flow("flow1", max_workers=4, max_retries=5)
        mgr1.register_flow("flow2", enable_checkpoints=False)

        mgr2 = FlowStateManager(state_file)
        mgr2.load()
        e1 = mgr2.get_entry("flow1")
        assert e1.max_workers == 4
        assert e1.max_retries == 5
        e2 = mgr2.get_entry("flow2")
        assert e2.enable_checkpoints is False

    def test_empty_state(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        mgr.load()
        assert mgr.get_all_entries() == []
        assert mgr.get_flows_to_recover() == []

    def test_get_entry_not_found(self, tmp_path):
        mgr = FlowStateManager(str(tmp_path / "running.json"))
        assert mgr.get_entry("nonexistent") is None


# ---------------------------------------------------------------------------
