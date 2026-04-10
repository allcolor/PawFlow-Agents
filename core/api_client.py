"""HTTP API client for PawFlow GUI -- connects to a running PawFlow API server."""

import json
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode


class ApiError(Exception):
    """API call error."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class PawFlowApiClient:
    """HTTP client for PawFlow REST API.

    Usage:
        client = PawFlowApiClient("http://localhost:8000")
        client.login("admin", "admin")
        flows = client.list_flows()
    """

    def __init__(self, base_url: str = "http://localhost:8000", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = 30

    def _request(self, method: str, path: str, data: Any = None,
                 params: Dict = None) -> Any:
        """Make HTTP request to API."""
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode(params)

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                content = resp.read().decode("utf-8")
                if content:
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return content
                return None
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(err_body).get("detail", err_body)
            except (json.JSONDecodeError, AttributeError):
                detail = err_body
            raise ApiError(f"{method} {path}: {detail}", e.code)
        except URLError as e:
            raise ApiError(f"Connection error: {e.reason}")

    def _get(self, path, **params):
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def _post(self, path, data=None, **params):
        return self._request("POST", path, data=data, params={k: v for k, v in params.items() if v is not None})

    def _put(self, path, data=None):
        return self._request("PUT", path, data=data)

    def _delete(self, path):
        return self._request("DELETE", path)

    # === Auth ===

    def login(self, username: str, password: str) -> Dict:
        """Login and store session token."""
        result = self._post("/api/v1/auth/login", {"username": username, "password": password})
        self.token = result.get("session_id", "")
        return result

    def logout(self):
        """Logout current session."""
        result = self._post("/api/v1/auth/logout")
        self.token = ""
        return result

    def me(self) -> Dict:
        return self._get("/api/v1/auth/me")

    def get_security_status(self) -> Dict:
        return self._get("/api/v1/system/security/status")

    # === Flows ===

    def list_flows(self) -> List[Dict]:
        return self._get("/api/v1/flows/")

    def get_flow(self, flow_id: str) -> Dict:
        return self._get(f"/api/v1/flows/{flow_id}")

    def create_flow(self, config: Dict) -> Dict:
        return self._post("/api/v1/flows/", {"config": config})

    def update_flow(self, flow_id: str, config: Dict) -> Dict:
        return self._put(f"/api/v1/flows/{flow_id}", {"config": config})

    def delete_flow(self, flow_id: str) -> Dict:
        return self._delete(f"/api/v1/flows/{flow_id}")

    def validate_flow(self, config: Dict) -> Dict:
        return self._post("/api/v1/flows/validate", {"config": config})

    # === Tasks & Services ===

    def list_tasks(self) -> List[Dict]:
        return self._get("/api/v1/tasks/")

    def get_task_schema(self, task_type: str) -> Dict:
        return self._get(f"/api/v1/tasks/{task_type}/schema")

    def list_services(self) -> List[Dict]:
        return self._get("/api/v1/tasks/services")

    # === Execution ===

    def execute_batch(self, flow_id: str = "", flow_config: Dict = None,
                      input_data: str = "", variables: Dict = None,
                      parameters: Dict = None) -> Dict:
        body = {}
        if flow_id:
            body["flow_id"] = flow_id
        if flow_config:
            body["flow_config"] = flow_config
        if input_data:
            body["input_data"] = input_data
        if variables:
            body["variables"] = variables
        if parameters:
            body["parameters"] = parameters
        return self._post("/api/v1/execution/batch", body)

    def start_continuous(self, executor_id: str, flow_id: str = "",
                         flow_config: Dict = None, parameters: Dict = None) -> Dict:
        body = {}
        if flow_id:
            body["flow_id"] = flow_id
        if flow_config:
            body["flow_config"] = flow_config
        if parameters:
            body["parameters"] = parameters
        return self._post(f"/api/v1/execution/continuous/start/{executor_id}", body)

    def stop_continuous(self, executor_id: str) -> Dict:
        return self._post(f"/api/v1/execution/continuous/{executor_id}/stop")

    def get_continuous_status(self, executor_id: str) -> Dict:
        return self._get(f"/api/v1/execution/continuous/{executor_id}/status")

    def inject_flowfile(self, executor_id: str, content: str = "",
                        attributes: Dict = None) -> Dict:
        body = {"content": content}
        if attributes:
            body["attributes"] = attributes
        return self._post(f"/api/v1/execution/continuous/{executor_id}/inject", body)

    # === Monitoring ===

    def get_bulletins(self, level: str = None, limit: int = None) -> List:
        return self._get("/api/v1/monitoring/bulletins", level=level, limit=limit)

    def get_bulletin_counts(self) -> Dict:
        return self._get("/api/v1/monitoring/bulletins/counts")

    def get_provenance_stats(self) -> Dict:
        return self._get("/api/v1/monitoring/provenance/stats")

    def get_streaming_stats(self) -> Dict:
        return self._get("/api/v1/monitoring/streaming")

    # === Workers ===

    def list_workers(self) -> List[Dict]:
        return self._get("/api/v1/workers/")

    def get_worker_health(self) -> Dict:
        return self._get("/api/v1/workers/health")

    # === Plugins ===

    def list_plugins(self) -> List[Dict]:
        return self._get("/api/v1/plugins/")

    # === System ===

    def health(self) -> Dict:
        return self._get("/api/v1/system/health")

    def system_info(self) -> Dict:
        return self._get("/api/v1/system/info")

    def get_cluster_status(self) -> Dict:
        return self._get("/api/v1/system/cluster/status")

    def get_cluster_instances(self) -> List[Dict]:
        return self._get("/api/v1/system/cluster/instances")

    # === Audit ===

    def get_audit_log(self, action: str = None, user: str = None,
                      limit: int = 100) -> List:
        return self._get("/api/v1/system/audit", action=action, user=user, limit=limit)

    def get_audit_stats(self) -> Dict:
        return self._get("/api/v1/system/audit/stats")


def get_api_client() -> Optional[PawFlowApiClient]:
    """Get API client from session state, or None if in direct mode."""
    try:
        import streamlit as st
        return st.session_state.get("api_client")
    except ImportError:
        return None


def is_api_mode() -> bool:
    """Check if GUI is running in API mode."""
    try:
        import streamlit as st
        return st.session_state.get("api_mode", False)
    except ImportError:
        return False
