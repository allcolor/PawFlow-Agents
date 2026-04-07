"""Tests for PlanStore — file-based plan storage.

Tests cover:
- save / get — create and retrieve a plan
- list_plans — filter by user/conversation
- update step status via save
- delete — remove a plan
- delete_all — remove all plans for a conversation
- Persistence — create, reload from disk, verify
"""

import time
import uuid
import pytest

from core.plan_store import PlanStore, _PLANS_DIR


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset PlanStore singleton between tests."""
    PlanStore._instance = None
    yield
    PlanStore._instance = None


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Create a PlanStore that writes to a temp directory."""
    monkeypatch.setattr("core.plan_store._PLANS_DIR", tmp_path / "plans")
    return PlanStore()


def _plan(plan_id=None, title="Test Plan", steps=None):
    """Build a minimal plan dict."""
    return {
        "id": plan_id or uuid.uuid4().hex[:12],
        "title": title,
        "status": "active",
        "created_at": time.time(),
        "steps": steps or [
            {"id": "s1", "description": "Step 1", "status": "pending"},
            {"id": "s2", "description": "Step 2", "status": "pending"},
        ],
    }


USER = "testuser"
CONV = "conv-001"


# ── save / get ───────────────────────────────────────────────────────

class TestCreateAndGet:

    def test_save_and_get(self, store):
        plan = _plan()
        store.save(USER, CONV, plan)
        loaded = store.get(USER, CONV, plan["id"])
        assert loaded is not None
        assert loaded["id"] == plan["id"]
        assert loaded["title"] == "Test Plan"

    def test_get_nonexistent_returns_none(self, store):
        assert store.get(USER, CONV, "nonexistent") is None

    def test_save_requires_id(self, store):
        with pytest.raises(ValueError, match="id"):
            store.save(USER, CONV, {"title": "no id"})

    def test_save_requires_user_id(self, store):
        with pytest.raises(ValueError, match="user_id"):
            store.save("", CONV, _plan())


# ── list_plans ───────────────────────────────────────────────────────

class TestListPlans:

    def test_list_empty(self, store):
        assert store.list_plans(USER, CONV) == []

    def test_list_returns_saved(self, store):
        p1 = _plan(title="Plan A")
        p2 = _plan(title="Plan B")
        store.save(USER, CONV, p1)
        store.save(USER, CONV, p2)
        plans = store.list_plans(USER, CONV)
        assert len(plans) == 2
        titles = {p["title"] for p in plans}
        assert titles == {"Plan A", "Plan B"}

    def test_list_isolated_by_conv(self, store):
        p1 = _plan(title="In conv1")
        p2 = _plan(title="In conv2")
        store.save(USER, "conv-1", p1)
        store.save(USER, "conv-2", p2)
        plans = store.list_plans(USER, "conv-1")
        assert len(plans) == 1
        assert plans[0]["title"] == "In conv1"

    def test_list_isolated_by_user(self, store):
        p1 = _plan(title="Alice plan")
        p2 = _plan(title="Bob plan")
        store.save("alice", CONV, p1)
        store.save("bob", CONV, p2)
        plans = store.list_plans("alice", CONV)
        assert len(plans) == 1
        assert plans[0]["title"] == "Alice plan"

    def test_list_sorted_by_created_at(self, store):
        now = time.time()
        p1 = _plan(title="Older")
        p1["created_at"] = now - 100
        p2 = _plan(title="Newer")
        p2["created_at"] = now
        store.save(USER, CONV, p1)
        store.save(USER, CONV, p2)
        plans = store.list_plans(USER, CONV)
        assert plans[0]["title"] == "Newer"
        assert plans[1]["title"] == "Older"


# ── update step (via save) ───────────────────────────────────────────

class TestUpdateStep:

    def test_update_step_status(self, store):
        plan = _plan()
        store.save(USER, CONV, plan)
        # Simulate updating a step
        loaded = store.get(USER, CONV, plan["id"])
        for step in loaded["steps"]:
            if step["id"] == "s1":
                step["status"] = "done"
        store.save(USER, CONV, loaded)
        reloaded = store.get(USER, CONV, plan["id"])
        s1 = next(s for s in reloaded["steps"] if s["id"] == "s1")
        assert s1["status"] == "done"

    def test_update_plan_status(self, store):
        plan = _plan()
        store.save(USER, CONV, plan)
        loaded = store.get(USER, CONV, plan["id"])
        loaded["status"] = "completed"
        store.save(USER, CONV, loaded)
        reloaded = store.get(USER, CONV, plan["id"])
        assert reloaded["status"] == "completed"


# ── delete ───────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, store):
        plan = _plan()
        store.save(USER, CONV, plan)
        assert store.delete(USER, CONV, plan["id"]) is True
        assert store.get(USER, CONV, plan["id"]) is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete(USER, CONV, "nope") is False

    def test_delete_all(self, store):
        p1 = _plan()
        p2 = _plan()
        store.save(USER, CONV, p1)
        store.save(USER, CONV, p2)
        store.delete_all(USER, CONV)
        assert store.list_plans(USER, CONV) == []

    def test_delete_does_not_affect_other_conv(self, store):
        p1 = _plan(title="Keep")
        p2 = _plan(title="Remove")
        store.save(USER, "conv-keep", p1)
        store.save(USER, "conv-del", p2)
        store.delete_all(USER, "conv-del")
        assert len(store.list_plans(USER, "conv-keep")) == 1
        assert store.list_plans(USER, "conv-del") == []


# ── Persistence ──────────────────────────────────────────────────────

class TestPersistence:

    def test_survives_new_instance(self, store, tmp_path, monkeypatch):
        """Create a plan, build a fresh PlanStore, verify data is on disk."""
        plan = _plan(title="Persistent")
        store.save(USER, CONV, plan)

        # Create a brand new PlanStore pointing at the same directory
        store2 = PlanStore()
        loaded = store2.get(USER, CONV, plan["id"])
        assert loaded is not None
        assert loaded["title"] == "Persistent"

    def test_data_written_as_json(self, store, tmp_path):
        """Verify the file on disk is valid JSON."""
        import json
        plan = _plan(plan_id="check-json")
        store.save(USER, CONV, plan)
        # Read the raw file
        safe_user = USER.replace("/", "_").replace("\\", "_")
        safe_conv = CONV.replace(":", "_")
        path = tmp_path / "plans" / safe_user / safe_conv / "check-json.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"] == "check-json"
        assert data["title"] == "Test Plan"
