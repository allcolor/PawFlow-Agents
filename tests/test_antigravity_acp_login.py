"""Antigravity ACP server login: driver, container script, action branch, token storage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import core.paths as _paths


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "acp_runtime_agent.py"
DRIVER = ROOT / "docker" / "claude-code" / "agy_acp_login.py"
SCRIPT = ROOT / "docker" / "claude-code" / "agy_acp_auth_login.sh"


# -- driver against the fixture ------------------------------------------------


def _run_driver(tmp_path, method):
    wrapper = tmp_path / "agy_acp_server"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FIXTURE}\n", encoding="utf-8")
    wrapper.chmod(0o755)
    result_path = tmp_path / "result.json"
    env = dict(os.environ)
    env.update({
        "AGY_ACP_BIN": str(wrapper),
        "AGY_ACP_AUTH_METHOD": method,
        "AGY_ACP_LOGIN_RESULT": str(result_path),
        "AGY_ACP_LOGIN_STDERR": str(tmp_path / "stderr.log"),
        "AGY_ACP_LOGIN_TIMEOUT": "20",
        "PAWFLOW_ACP_FIXTURE_AUTH": "antigravity",
        "GEMINI_HOME": str(tmp_path / ".gemini"),
    })
    completed = subprocess.run(
        [sys.executable, str(DRIVER)], env=env, capture_output=True, text=True, timeout=60)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return completed, payload


def test_driver_authenticates_with_an_advertised_method(tmp_path):
    completed, payload = _run_driver(tmp_path, "oauth-personal")
    assert completed.returncode == 0, completed.stderr
    assert payload == {"ok": True, "method": "oauth-personal"}
    assert "Authenticated" in completed.stdout


def test_driver_refuses_a_method_the_server_does_not_advertise(tmp_path):
    completed, payload = _run_driver(tmp_path, "api-key-nope")
    assert completed.returncode == 2
    assert payload["ok"] is False
    assert "oauth-personal" in payload["error"]
    assert payload["advertised"] == [
        "oauth-personal", "oauth-business", "gemini-api-key", "agent-platform"]


# -- container script -----------------------------------------------------------


def test_login_script_keeps_a_visible_tty_and_isolated_home():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "xterm" in script
    assert 'export GEMINI_HOME="/workspace/.gemini"' in script
    assert "python3 /opt/pawflow/agy_acp_login.py" in script
    assert 'export BROWSER="/usr/local/bin/open-browser"' in script
    assert "unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI" in script
    assert "/tmp/agy-acp-login.result.json" in script
    dockerfile = (ROOT / "docker" / "claude-code" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY agy_acp_auth_login.sh /opt/pawflow/agy_acp_auth_login.sh" in dockerfile
    assert "COPY agy_acp_login.py /opt/pawflow/agy_acp_login.py" in dockerfile
    assert "ANTIGRAVITY_ACP_SHA256=" in dockerfile


def test_service_form_offers_the_login_through_the_agy_dialog():
    from services.llm_connection import LLMConnectionService

    actions = LLMConnectionService({"provider": "antigravity-acp", "auth_mode": "none"}).get_service_actions()
    entry = next(item for item in actions if item["id"] == "antigravity_acp_server_login")
    assert entry["when"] == {"provider": ["antigravity-acp"]}
    assert entry["server_action"] == "agy_server_login"
    assert entry["flow"] == "gemini_login_server"


# -- action branch ------------------------------------------------------------


class _FlowFile:
    def __init__(self):
        self._content = b""

    def get_attribute(self, _name):
        return ""

    def set_content(self, data):
        self._content = data

    def payload(self):
        return json.loads(self._content or b"{}")


def _patch_registry(monkeypatch, config):
    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve_definition(self, *a, **k):
            return types.SimpleNamespace(service_id="agy-acp", config=config)

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)


def _run_login(monkeypatch, body):
    from tasks.ai.actions import _sf_acp, _sf_k8

    captured = {"argv": []}

    class _Thread:
        def __init__(self, target=None, **kwargs):
            captured["target"] = target

        def start(self):
            captured["target"]()

    def _fake_run(argv, *a, **k):
        captured["argv"].append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")

    monkeypatch.setattr(_sf_acp.threading, "Thread", _Thread)
    monkeypatch.setattr(_sf_acp.subprocess, "run", _fake_run)
    monkeypatch.setattr(_sf_acp, "_docker_published_host", lambda: "127.0.0.1")
    monkeypatch.setattr(_sf_acp, "_docker_container_ip", lambda *_a: "172.17.0.2")
    monkeypatch.setattr(_sf_acp, "_wait_for_vnc_login_backend", lambda *a, **k: True)
    monkeypatch.setattr(_sf_acp, "_ensure_vnc_routes", lambda *_a: None)
    monkeypatch.setattr("services.vnc_proxy.register_session", lambda *a, **k: "tok")
    monkeypatch.setattr("services.vnc_proxy.update_session_ready", lambda *_a: None)
    published = []

    class _Bus:
        @staticmethod
        def instance():
            return _Bus()

        def publish_event(self, conversation_id, name, payload):
            published.append((conversation_id, name, payload))

    monkeypatch.setattr("core.conversation_event_bus.ConversationEventBus", _Bus)
    monkeypatch.setattr(_sf_k8, "_credential_provider_for_service", lambda *a, **k: "")
    flowfile = _FlowFile()
    helpers = tuple(lambda *a, **k: "" for _ in range(6))
    _sf_k8._handle_sf_k8(None, "agy_server_login", body, None, "user-acp", flowfile, helpers)
    return captured, published, flowfile


def test_agy_login_action_branches_to_the_acp_container(monkeypatch):
    _patch_registry(monkeypatch, {"provider": "antigravity-acp"})
    body = {"service_id": "agy-acp", "conversation_id": "conv",
            "config": {"antigravity_acp_auth_method": "oauth-business"}}
    captured, published, flowfile = _run_login(monkeypatch, body)

    response = flowfile.payload()
    assert response["ok"] is True and response["cli"] == "agy"
    docker_run = [a for a in captured["argv"] if "run" in a and "--detach" in a][0]
    assert docker_run[docker_run.index("--name") + 1].startswith("pawflow-agyacp-login-")
    assert "--label" in docker_run
    assert "AGY_ACP_AUTH_METHOD=oauth-business" in docker_run
    assert docker_run[-1] == "/opt/pawflow/agy_acp_auth_login.sh"
    assert any(":/opt/pawflow/agy_acp_login.py:ro" in item for item in docker_run)
    assert published and published[0][1] == "vnc_login_ready"
    assert published[0][2]["cli"] == "agy"


def test_agy_login_action_refuses_api_key_methods_for_the_browser_flow(monkeypatch):
    _patch_registry(monkeypatch, {"provider": "antigravity-acp",
                                  "antigravity_acp_auth_method": "gemini-api-key"})
    captured, published, flowfile = _run_login(
        monkeypatch, {"service_id": "agy-acp", "conversation_id": "conv"})
    assert "does not use a browser login" in flowfile.payload()["error"]
    assert captured["argv"] == [] and published == []


def test_status_stores_the_token_under_the_service_home(monkeypatch, tmp_path):
    from tasks.ai.actions import _sf_acp, _sf_k8
    from core.antigravity_acp_pool import AntigravityAcpPool

    monkeypatch.setattr(_paths, "RUNTIME_DIR", tmp_path / "runtime")
    _patch_registry(monkeypatch, {"provider": "antigravity-acp"})
    session = {"container": "pawflow-agyacp-login-1", "ready": True,
               "launch_time": 1_000_000_000.0, "service_id": "agy-acp"}
    monkeypatch.setattr(_sf_acp.time, "time", lambda: 1_000_000_010.0)
    monkeypatch.setattr("services.vnc_proxy._sessions", {"sess-1": session})
    unregistered = []
    monkeypatch.setattr("services.vnc_proxy.unregister_session", unregistered.append)
    removed = []

    def _fake_run(argv, *a, **k):
        argv = list(argv)
        if "rm" in argv:
            removed.append(argv[-1])
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        path = argv[-1]
        if path.endswith("result.json"):
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "method": "oauth-personal"}), stderr="")
        if path.endswith("acp_token.json"):
            return types.SimpleNamespace(returncode=0, stdout='{"token": "secret"}\n', stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no such file")

    monkeypatch.setattr(_sf_acp.subprocess, "run", _fake_run)
    flowfile = _FlowFile()
    helpers = tuple(lambda *a, **k: "" for _ in range(6))
    _sf_k8._handle_sf_k8(
        None, "agy_server_login_status",
        {"session_id": "sess-1", "service_id": "agy-acp", "conversation_id": "conv"},
        None, "user-acp", flowfile, helpers)

    response = flowfile.payload()
    assert response["ok"] is True and response["files"] == ["acp_token.json"]
    token = AntigravityAcpPool.home_dir("user-acp", "agy-acp") / ".gemini" / "antigravity-acp" / "acp_token.json"
    assert token.read_text(encoding="utf-8") == '{"token": "secret"}\n'
    assert oct(token.stat().st_mode & 0o777) == "0o600"
    assert removed == ["pawflow-agyacp-login-1"]
    assert unregistered == ["sess-1"]


def test_status_reports_the_driver_error_and_cleans_up(monkeypatch, tmp_path):
    from tasks.ai.actions import _sf_acp, _sf_k8

    monkeypatch.setattr(_paths, "RUNTIME_DIR", tmp_path / "runtime")
    _patch_registry(monkeypatch, {"provider": "antigravity-acp"})
    session = {"container": "pawflow-agyacp-login-2", "ready": True,
               "launch_time": 1_000_000_000.0, "service_id": "agy-acp"}
    monkeypatch.setattr(_sf_acp.time, "time", lambda: 1_000_000_010.0)
    monkeypatch.setattr("services.vnc_proxy._sessions", {"sess-2": session})
    monkeypatch.setattr("services.vnc_proxy.unregister_session", lambda *_a: None)

    def _fake_run(argv, *a, **k):
        if "rm" in list(argv):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps({"ok": False, "error": "authenticate failed: denied"}), stderr="")

    monkeypatch.setattr(_sf_acp.subprocess, "run", _fake_run)
    flowfile = _FlowFile()
    helpers = tuple(lambda *a, **k: "" for _ in range(6))
    _sf_k8._handle_sf_k8(
        None, "agy_server_login_status", {"session_id": "sess-2", "service_id": "agy-acp"},
        None, "user-acp", flowfile, helpers)
    assert "denied" in flowfile.payload()["error"]
