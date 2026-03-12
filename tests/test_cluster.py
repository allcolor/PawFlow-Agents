"""Tests for engine.cluster — ClusterState, ClusterCoordinator, multi-instance."""

import json
import os
import time
import threading
import pytest

from engine.cluster import (
    InstanceRole,
    InstanceInfo,
    ClusterState,
    ClusterCoordinator,
)


# ---------------------------------------------------------------------------
# ClusterState tests
# ---------------------------------------------------------------------------

class TestClusterState:

    def test_register_instance(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="abc123",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        path = tmp_path / "instance_abc123.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["instance_id"] == "abc123"
        assert data["role"] == "standby"
        assert data["host"] == "localhost"
        assert data["port"] == 8081

    def test_get_instances(self, tmp_path):
        state = ClusterState(str(tmp_path))
        for i in range(3):
            info = InstanceInfo(
                instance_id=f"inst{i}",
                role=InstanceRole.STANDBY,
                host="localhost",
                port=8080 + i,
            )
            state.register_instance(info)
        instances = state.get_instances()
        assert len(instances) == 3
        ids = {inst.instance_id for inst in instances}
        assert ids == {"inst0", "inst1", "inst2"}

    def test_update_heartbeat(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="hb1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
            last_heartbeat=time.time() - 100,
        )
        state.register_instance(info)
        old_hb = info.last_heartbeat
        state.update_heartbeat("hb1")
        instances = state.get_instances()
        assert len(instances) == 1
        assert instances[0].last_heartbeat > old_hb

    def test_update_heartbeat_missing_instance(self, tmp_path):
        state = ClusterState(str(tmp_path))
        # Should not raise
        state.update_heartbeat("nonexistent")

    def test_get_alive_instances(self, tmp_path):
        state = ClusterState(str(tmp_path))
        alive = InstanceInfo(
            instance_id="alive1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
            last_heartbeat=time.time(),
        )
        dead = InstanceInfo(
            instance_id="dead1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8082,
            last_heartbeat=time.time() - 60,
        )
        state.register_instance(alive)
        state.register_instance(dead)
        alive_instances = state.get_alive_instances(timeout=30.0)
        assert len(alive_instances) == 1
        assert alive_instances[0].instance_id == "alive1"

    def test_remove_instance(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="rm1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        assert len(state.get_instances()) == 1
        state.remove_instance("rm1")
        assert len(state.get_instances()) == 0

    def test_remove_nonexistent_instance(self, tmp_path):
        state = ClusterState(str(tmp_path))
        # Should not raise
        state.remove_instance("doesnotexist")

    def test_claim_coordinator(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        assert state.claim_coordinator("coord1") is True
        # Instance file should now show coordinator role
        instances = state.get_instances()
        coord = [i for i in instances if i.instance_id == "coord1"][0]
        assert coord.role == InstanceRole.COORDINATOR

    def test_claim_coordinator_blocked_by_existing(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info1 = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        info2 = InstanceInfo(
            instance_id="coord2",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8082,
        )
        state.register_instance(info1)
        state.register_instance(info2)
        assert state.claim_coordinator("coord1") is True
        # Second claim should fail because coord1 is alive
        assert state.claim_coordinator("coord2") is False

    def test_release_coordinator(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        state.claim_coordinator("coord1")
        state.release_coordinator("coord1")
        lock_path = tmp_path / "coordinator.lock"
        assert not lock_path.exists()

    def test_release_coordinator_wrong_id(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        state.claim_coordinator("coord1")
        # Releasing with wrong id should not remove lock
        state.release_coordinator("wrong_id")
        lock_path = tmp_path / "coordinator.lock"
        assert lock_path.exists()

    def test_get_coordinator(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.COORDINATOR,
            host="localhost",
            port=8081,
        )
        state.register_instance(info)
        coord = state.get_coordinator()
        assert coord is not None
        assert coord.instance_id == "coord1"

    def test_get_coordinator_returns_none_when_dead(self, tmp_path):
        state = ClusterState(str(tmp_path))
        info = InstanceInfo(
            instance_id="coord1",
            role=InstanceRole.COORDINATOR,
            host="localhost",
            port=8081,
            last_heartbeat=time.time() - 60,
        )
        state.register_instance(info)
        assert state.get_coordinator(timeout=30.0) is None

    def test_ignores_corrupt_files(self, tmp_path):
        state = ClusterState(str(tmp_path))
        # Write a corrupt file
        (tmp_path / "instance_corrupt.json").write_text("not valid json{{{")
        # Should return empty list without raising
        assert state.get_instances() == []


# ---------------------------------------------------------------------------
# InstanceInfo tests
# ---------------------------------------------------------------------------

class TestInstanceInfo:

    def test_is_alive_true(self):
        info = InstanceInfo(
            instance_id="x",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
            last_heartbeat=time.time(),
        )
        assert info.is_alive(timeout=30.0) is True

    def test_is_alive_false(self):
        info = InstanceInfo(
            instance_id="x",
            role=InstanceRole.STANDBY,
            host="localhost",
            port=8081,
            last_heartbeat=time.time() - 60,
        )
        assert info.is_alive(timeout=30.0) is False


# ---------------------------------------------------------------------------
# ClusterCoordinator tests
# ---------------------------------------------------------------------------

class TestClusterCoordinator:

    def test_start_stop(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc.start()
        assert cc._running is True
        assert cc._heartbeat_thread is not None
        cc.stop()
        assert cc._running is False

    def test_auto_promote_first_instance(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
            auto_promote=True,
        )
        cc.start()
        try:
            assert cc.is_coordinator is True
            assert cc.role == InstanceRole.COORDINATOR
        finally:
            cc.stop()

    def test_no_auto_promote(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
            auto_promote=False,
        )
        cc.start()
        try:
            assert cc.is_coordinator is False
            assert cc.role == InstanceRole.STANDBY
        finally:
            cc.stop()

    def test_heartbeat_updates(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc.start()
        try:
            initial_hb = cc._info.last_heartbeat
            # Wait for at least one heartbeat cycle
            time.sleep(0.5)
            instances = cc._state.get_instances()
            inst = [i for i in instances if i.instance_id == cc.instance_id]
            assert len(inst) == 1
            assert inst[0].last_heartbeat >= initial_hb
        finally:
            cc.stop()

    def test_promoted_callback(self, tmp_path):
        events = []
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
            auto_promote=True,
        )
        cc.on("promoted", lambda **kw: events.append(kw))
        cc.start()
        try:
            assert len(events) == 1
            assert events[0]["instance_id"] == cc.instance_id
        finally:
            cc.stop()

    def test_get_status(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc.start()
        try:
            # Ensure heartbeat has written at least once
            import time
            time.sleep(0.3)
            status = cc.get_status()
            assert status["instance_id"] == cc.instance_id
            assert status["role"] == "coordinator"
            assert status["total_instances"] >= 1
            assert status["coordinator"] == cc.instance_id
        finally:
            cc.stop()

    def test_get_instances(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc.start()
        try:
            instances = cc.get_instances()
            assert len(instances) >= 1
            self_inst = [i for i in instances if i["is_self"]]
            assert len(self_inst) == 1
            assert self_inst[0]["instance_id"] == cc.instance_id
        finally:
            cc.stop()

    def test_step_down(self, tmp_path):
        events = []
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc.on("demoted", lambda **kw: events.append(kw))
        cc.start()
        try:
            assert cc.is_coordinator is True
            cc.step_down()
            assert cc.is_coordinator is False
            assert cc.role == InstanceRole.STANDBY
            assert len(events) == 1
        finally:
            cc.stop()

    def test_manual_promote(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
            auto_promote=False,
        )
        cc.start()
        try:
            assert cc.is_coordinator is False
            result = cc.promote_to_coordinator()
            assert result is True
            assert cc.is_coordinator is True
        finally:
            cc.stop()

    def test_metadata_preserved(self, tmp_path):
        cc = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
            metadata={"region": "us-east-1", "gpu": "true"},
        )
        cc.start()
        try:
            instances = cc.get_instances()
            self_inst = [i for i in instances if i["is_self"]][0]
            assert self_inst["metadata"]["region"] == "us-east-1"
            assert self_inst["metadata"]["gpu"] == "true"
        finally:
            cc.stop()


# ---------------------------------------------------------------------------
# Multi-instance tests
# ---------------------------------------------------------------------------

class TestMultiInstance:

    def test_second_instance_stays_standby(self, tmp_path):
        cc1 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc2 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc1.start()
        cc2.start()
        try:
            assert cc1.is_coordinator is True
            assert cc2.is_coordinator is False
            assert cc2.role == InstanceRole.STANDBY
        finally:
            cc1.stop()
            cc2.stop()

    def test_promotion_after_coordinator_death(self, tmp_path):
        """When coordinator dies, a standby should promote itself."""
        cc1 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.1,
            heartbeat_timeout=0.5,
        )
        cc2 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.1,
            heartbeat_timeout=0.5,
        )
        promoted_events = []
        cc2.on("promoted", lambda **kw: promoted_events.append(kw))

        cc1.start()
        cc2.start()
        assert cc1.is_coordinator is True
        assert cc2.is_coordinator is False

        # Kill cc1 (stop without cleanup to simulate crash — but we still
        # need to stop heartbeats so it looks dead)
        cc1._running = False
        if cc1._heartbeat_thread:
            cc1._heartbeat_thread.join(timeout=2)
        if cc1._election_thread:
            cc1._election_thread.join(timeout=2)

        # Wait for heartbeat timeout + multiple election cycles
        time.sleep(3.0)

        try:
            assert cc2.is_coordinator is True
            assert len(promoted_events) >= 1
        finally:
            cc1.stop()
            cc2.stop()

    def test_both_visible_in_instances(self, tmp_path):
        cc1 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc2 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.2,
            heartbeat_timeout=30.0,
        )
        cc1.start()
        cc2.start()
        try:
            import time
            time.sleep(0.3)  # Wait for heartbeats to write
            instances = cc1.get_instances()
            ids = {i["instance_id"] for i in instances}
            assert cc1.instance_id in ids
            assert cc2.instance_id in ids
        finally:
            cc1.stop()
            cc2.stop()

    def test_instance_left_callback(self, tmp_path):
        """When an instance dies, the election loop should detect it."""
        left_events = []
        cc1 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.1,
            heartbeat_timeout=0.5,
        )
        cc2 = ClusterCoordinator(
            state_dir=str(tmp_path),
            heartbeat_interval=0.1,
            heartbeat_timeout=0.5,
        )
        cc2.on("instance_left", lambda **kw: left_events.append(kw))

        cc1.start()
        cc2.start()

        # Let cc2 discover cc1
        time.sleep(0.3)

        # Kill cc1
        cc1._running = False
        if cc1._heartbeat_thread:
            cc1._heartbeat_thread.join(timeout=2)

        # Wait for timeout + election cycle
        time.sleep(1.5)

        try:
            left_ids = [e.get("instance_id") for e in left_events]
            assert cc1.instance_id in left_ids
        finally:
            cc1.stop()
            cc2.stop()
