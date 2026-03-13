"""Tests for the REST API."""

import json
import pytest
from fastapi.testclient import TestClient

from tasks import register_all_tasks
register_all_tasks()

from api.app import app
from core.security import SecurityManager, Role

client = TestClient(app)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_security(tmp_path):
    """Use temp files for security config in all tests."""
    import core.security as sec
    orig_config = sec.SECURITY_CONFIG_PATH
    orig_users = sec.USERS_PATH
    sec.SECURITY_CONFIG_PATH = str(tmp_path / "security.json")
    sec.USERS_PATH = str(tmp_path / "users.json")
    SecurityManager._instance = None
    yield
    sec.SECURITY_CONFIG_PATH = orig_config
    sec.USERS_PATH = orig_users
    SecurityManager._instance = None


@pytest.fixture
def admin_token():
    """Get an admin session token."""
    resp = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin",
    })
    assert resp.status_code == 200
    return resp.json()["session_id"]


@pytest.fixture
def auth_headers(admin_token):
    """Auth headers with admin token."""
    return {"Authorization": f"Bearer {admin_token}"}


# ============================================================================
# Root & System
# ============================================================================

class TestRoot:

    def test_root(self):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "PyFi2 API"
        assert data["status"] == "running"

    def test_health(self):
        resp = client.get("/api/v1/system/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_security_status(self):
        resp = client.get("/api/v1/system/security/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data

    def test_prometheus_metrics(self):
        resp = client.get("/api/v1/system/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "pyfi2_info" in text
        assert "pyfi2_uptime_seconds" in text
        assert "pyfi2_tasks_registered_total" in text
        assert "# TYPE" in text

    def test_system_info(self, auth_headers):
        resp = client.get("/api/v1/system/info", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks_available"] > 0
        assert data["services_available"] > 0


# ============================================================================
# Auth
# ============================================================================

class TestAuth:

    def test_login_success(self):
        resp = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert "session_id" in data

    def test_login_failure(self):
        resp = client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "wrong",
        })
        assert resp.status_code == 401

    def test_me_no_auth(self):
        # Auth is disabled by default, so should work
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200

    def test_me_with_auth(self, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200

    def test_logout(self, admin_token):
        resp = client.post("/api/v1/auth/logout", headers={
            "Authorization": f"Bearer {admin_token}",
        })
        assert resp.status_code == 200

    def test_auth_required_when_enabled(self, auth_headers):
        # Enable auth
        sm = SecurityManager.get_instance()
        sm.enable_auth(True)

        # Request without auth should fail
        resp = client.get("/api/v1/system/info")
        assert resp.status_code == 401

        # Request with auth should work
        resp = client.get("/api/v1/system/info", headers=auth_headers)
        assert resp.status_code == 200

    def test_permission_denied(self, auth_headers):
        sm = SecurityManager.get_instance()
        sm.enable_auth(True)
        sm.create_user("viewer", "pass", Role.VIEWER)

        # Login as viewer
        resp = client.post("/api/v1/auth/login", json={
            "username": "viewer", "password": "pass",
        })
        viewer_token = resp.json()["session_id"]
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        # Viewer should not be able to manage users
        resp = client.get("/api/v1/auth/users", headers=viewer_headers)
        assert resp.status_code == 403


# ============================================================================
# User Management
# ============================================================================

class TestUserManagement:

    def test_create_user(self, auth_headers):
        resp = client.post("/api/v1/auth/users", json={
            "username": "bob", "password": "secret", "role": "editor",
        }, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["username"] == "bob"

    def test_list_users(self, auth_headers):
        resp = client.get("/api/v1/auth/users", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1  # at least admin

    def test_update_user(self, auth_headers):
        # Create user first
        client.post("/api/v1/auth/users", json={
            "username": "alice", "password": "pass", "role": "viewer",
        }, headers=auth_headers)

        resp = client.put("/api/v1/auth/users/alice", json={
            "role": "editor",
        }, headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_user(self, auth_headers):
        client.post("/api/v1/auth/users", json={
            "username": "todelete", "password": "pass", "role": "viewer",
        }, headers=auth_headers)

        resp = client.delete("/api/v1/auth/users/todelete", headers=auth_headers)
        assert resp.status_code == 200

    def test_cannot_delete_admin(self, auth_headers):
        resp = client.delete("/api/v1/auth/users/admin", headers=auth_headers)
        assert resp.status_code == 400

    def test_duplicate_user(self, auth_headers):
        client.post("/api/v1/auth/users", json={
            "username": "dup", "password": "pass", "role": "viewer",
        }, headers=auth_headers)
        resp = client.post("/api/v1/auth/users", json={
            "username": "dup", "password": "pass", "role": "viewer",
        }, headers=auth_headers)
        assert resp.status_code == 409


# ============================================================================
# API Keys
# ============================================================================

class TestApiKeys:

    def test_create_api_key(self, auth_headers):
        resp = client.post("/api/v1/auth/api-keys?description=test",
                           headers=auth_headers)
        assert resp.status_code == 200
        assert "key" in resp.json()

    def test_list_api_keys(self, auth_headers):
        client.post("/api/v1/auth/api-keys?description=list_test",
                     headers=auth_headers)
        resp = client.get("/api/v1/auth/api-keys", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_api_key_auth(self, auth_headers):
        # Create key
        resp = client.post("/api/v1/auth/api-keys?description=auth_test",
                           headers=auth_headers)
        key = resp.json()["key"]

        # Enable auth
        sm = SecurityManager.get_instance()
        sm.enable_auth(True)

        # Use API key as auth
        resp = client.get("/api/v1/system/info", headers={
            "Authorization": f"Bearer {key}",
        })
        assert resp.status_code == 200


# ============================================================================
# Roles
# ============================================================================

class TestRoles:

    def test_list_roles(self):
        resp = client.get("/api/v1/auth/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "admin" in data
        assert "viewer" in data
        assert "editor" in data
        assert "operator" in data


# ============================================================================
# Tasks & Services
# ============================================================================

class TestTasksServices:

    def test_list_tasks(self, auth_headers):
        resp = client.get("/api/v1/tasks/", headers=auth_headers)
        assert resp.status_code == 200
        types = [t["type"] for t in resp.json()]
        assert "log" in types
        assert "getFile" in types

    def test_get_task_schema(self, auth_headers):
        resp = client.get("/api/v1/tasks/log/schema", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "log"
        assert "parameters" in data

    def test_list_services(self, auth_headers):
        resp = client.get("/api/v1/tasks/services", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) > 0

    def test_unknown_task_schema(self, auth_headers):
        resp = client.get("/api/v1/tasks/nonexistent_xyz/schema", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================================
# Flows
# ============================================================================

class TestFlows:

    def test_list_flows(self, auth_headers):
        resp = client.get("/api/v1/flows/", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_and_get_flow(self, auth_headers):
        config = {
            "id": "test-api-flow",
            "name": "Test API Flow",
            "version": "1.0.0",
            "tasks": {
                "log1": {"type": "log", "parameters": {"message": "hello"}},
            },
            "relations": [],
        }
        resp = client.post("/api/v1/flows/", json={"config": config},
                           headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["id"] == "test-api-flow"

        # Get it back
        resp = client.get("/api/v1/flows/test-api-flow", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test API Flow"

    def test_validate_flow(self, auth_headers):
        config = {
            "id": "validate-test",
            "name": "Validate Test",
            "tasks": {
                "log1": {"type": "log", "parameters": {"message": "hi"}},
            },
            "relations": [],
        }
        resp = client.post("/api/v1/flows/validate", json={"config": config},
                           headers=auth_headers)
        assert resp.status_code == 200
        # Should be valid or have warnings but not crash


# ============================================================================
# Monitoring
# ============================================================================

class TestMonitoring:

    def test_get_bulletins(self, auth_headers):
        resp = client.get("/api/v1/monitoring/bulletins", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_bulletin_counts(self, auth_headers):
        resp = client.get("/api/v1/monitoring/bulletins/counts",
                          headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "INFO" in data
        assert "ERROR" in data

    def test_provenance_stats(self, auth_headers):
        resp = client.get("/api/v1/monitoring/provenance/stats",
                          headers=auth_headers)
        assert resp.status_code == 200
        assert "total_events" in resp.json()

    def test_streaming_stats(self, auth_headers):
        resp = client.get("/api/v1/monitoring/streaming", headers=auth_headers)
        assert resp.status_code == 200


# ============================================================================
# ============================================================================
# Workers
# ============================================================================

class TestWorkers:

    def test_list_workers(self, auth_headers):
        resp = client.get("/api/v1/workers/", headers=auth_headers)
        assert resp.status_code == 200
        workers = resp.json()
        assert len(workers) >= 1  # at least local worker

    def test_health_summary(self, auth_headers):
        resp = client.get("/api/v1/workers/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_workers" in data


# ============================================================================
# Plugins
# ============================================================================

class TestPlugins:

    def test_list_plugins(self, auth_headers):
        resp = client.get("/api/v1/plugins/", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
