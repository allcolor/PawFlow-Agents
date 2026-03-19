"""Tests for the GUI API client."""

import json
import io
import pytest
from unittest.mock import patch, MagicMock
from gui.services.api_client import OpenPawApiClient, ApiError


class TestApiClient:

    def setup_method(self):
        self.client = OpenPawApiClient("http://localhost:8000")

    def _mock_response(self, data, status=200):
        """Create a mock urllib response."""
        mock = MagicMock()
        content = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data.encode("utf-8")
        mock.read.return_value = content
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("gui.services.api_client.urlopen")
    def test_health(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"status": "healthy"})
        result = self.client.health()
        assert result["status"] == "healthy"

    @patch("gui.services.api_client.urlopen")
    def test_login(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "session_id": "tok123", "username": "admin", "role": "admin"
        })
        result = self.client.login("admin", "admin")
        assert self.client.token == "tok123"
        assert result["username"] == "admin"

    @patch("gui.services.api_client.urlopen")
    def test_list_flows(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response([
            {"id": "f1", "name": "Flow 1"}
        ])
        flows = self.client.list_flows()
        assert len(flows) == 1
        assert flows[0]["id"] == "f1"

    @patch("gui.services.api_client.urlopen")
    def test_create_flow(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"id": "new-flow"})
        config = {"id": "new-flow", "name": "New", "tasks": {}, "relations": []}
        result = self.client.create_flow(config)
        assert result["id"] == "new-flow"

    @patch("gui.services.api_client.urlopen")
    def test_execute_batch(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "status": "completed", "results": []
        })
        result = self.client.execute_batch(flow_id="f1", input_data="hello")
        assert result["status"] == "completed"

    @patch("gui.services.api_client.urlopen")
    def test_auth_header(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"ok": True})
        self.client.token = "mytoken"
        self.client.health()
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer mytoken"

    @patch("gui.services.api_client.urlopen")
    def test_error_handling(self, mock_urlopen):
        from urllib.error import HTTPError
        error_body = json.dumps({"detail": "Not found"}).encode()
        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, io.BytesIO(error_body)
        )
        with pytest.raises(ApiError) as exc_info:
            self.client.get_flow("nonexistent")
        assert exc_info.value.status_code == 404

    @patch("gui.services.api_client.urlopen")
    def test_connection_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")
        with pytest.raises(ApiError, match="Connection error"):
            self.client.health()

    @patch("gui.services.api_client.urlopen")
    def test_list_tasks(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response([
            {"type": "log", "name": "Log"}
        ])
        tasks = self.client.list_tasks()
        assert len(tasks) == 1

    @patch("gui.services.api_client.urlopen")
    def test_logout(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"ok": True})
        self.client.token = "tok"
        self.client.logout()
        assert self.client.token == ""

    @patch("gui.services.api_client.urlopen")
    def test_workers_and_plugins(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response([])
        assert self.client.list_workers() == []
        assert self.client.list_plugins() == []

    @patch("gui.services.api_client.urlopen")
    def test_monitoring(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"total_events": 42})
        result = self.client.get_provenance_stats()
        assert result["total_events"] == 42

    @patch("gui.services.api_client.urlopen")
    def test_validate_flow(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({"valid": True})
        result = self.client.validate_flow({"id": "test", "tasks": {}, "relations": []})
        assert result["valid"] is True

    def test_base_url_trailing_slash(self):
        client = OpenPawApiClient("http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"
