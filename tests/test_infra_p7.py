"""Tests for P7 infrastructure: Audit Log, Rate Limiter, WebSocket endpoints."""

import json
import time
import pytest

from tasks import register_all_tasks
register_all_tasks()


# ============================================================================
# Audit Log Tests
# ============================================================================

class TestAuditLog:

    @pytest.fixture(autouse=True)
    def reset_audit(self):
        from core.audit import AuditLog
        AuditLog.reset()
        yield
        AuditLog.reset()

    def test_log_and_query(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("flow.create", user="admin", resource_type="flow",
                  resource_id="my-flow", details={"name": "Test"})

        entries = audit.query()
        assert len(entries) == 1
        assert entries[0]["action"] == "flow.create"
        assert entries[0]["user"] == "admin"
        assert entries[0]["resource_id"] == "my-flow"

    def test_query_by_action(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("flow.create", user="admin", resource_type="flow", resource_id="f1")
        audit.log("flow.delete", user="admin", resource_type="flow", resource_id="f2")
        audit.log("user.create", user="admin", resource_type="user", resource_id="bob")

        results = audit.query(action="flow.*")
        assert len(results) == 2

        results = audit.query(action="user.create")
        assert len(results) == 1

    def test_query_by_user(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("flow.create", user="alice", resource_type="flow", resource_id="f1")
        audit.log("flow.create", user="bob", resource_type="flow", resource_id="f2")

        results = audit.query(user="alice")
        assert len(results) == 1
        assert results[0]["resource_id"] == "f1"

    def test_query_limit(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        for i in range(20):
            audit.log("test.action", user="user", resource_type="test", resource_id=str(i))

        results = audit.query(limit=5)
        assert len(results) == 5

    def test_query_newest_first(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("first", user="u", resource_type="t", resource_id="1")
        audit.log("second", user="u", resource_type="t", resource_id="2")

        results = audit.query()
        assert results[0]["action"] == "second"  # newest first

    def test_stats(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("flow.create", user="admin", resource_type="flow", resource_id="f1")
        audit.log("flow.delete", user="alice", resource_type="flow", resource_id="f2")

        stats = audit.get_stats()
        assert stats["total"] == 2
        assert stats["actions"]["flow.create"] == 1
        assert stats["users"]["admin"] == 1

    def test_clear(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("test", user="u", resource_type="t", resource_id="1")
        audit.clear()
        assert len(audit.query()) == 0

    def test_export_json(self):
        from core.audit import AuditLog
        audit = AuditLog.get_instance()
        audit.log("test", user="u", resource_type="t", resource_id="1")
        exported = audit.export_json()
        data = json.loads(exported)
        assert len(data) == 1
        assert data[0]["action"] == "test"

    def test_singleton(self):
        from core.audit import AuditLog
        a = AuditLog.get_instance()
        b = AuditLog.get_instance()
        assert a is b

    def test_thread_safety(self):
        """Concurrent writes should not lose entries."""
        import threading
        from core.audit import AuditLog
        audit = AuditLog.get_instance()

        def writer(n):
            for i in range(100):
                audit.log(f"thread.{n}", user="u", resource_type="t", resource_id=str(i))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(audit.query(limit=10000)) == 500


# ============================================================================
# Rate Limiter Tests
# ============================================================================
