import json
import subprocess
from pathlib import Path

import pytest

from core import ServiceError, ServiceFactory
from services.web_search_service import WebSearchConnectionService


def _connected_service(config):
    service = WebSearchConnectionService(config)
    service._connection = {
        "binary": "/usr/local/bin/search",
        "binary_available": True,
    }
    service._initialized = True
    return service


def test_web_search_service_is_registered_and_keys_are_sensitive():
    schema = WebSearchConnectionService({}).get_parameter_schema()

    assert ServiceFactory.get("webSearchConnection") is WebSearchConnectionService
    assert schema["brave_api_key"]["sensitive"] is True
    assert schema["brave_api_key"]["type"] == "password"
    assert "binary" not in schema


def test_web_search_service_schema_is_fully_supported_by_chat_ui():
    schema = WebSearchConnectionService({}).get_parameter_schema()
    ui = (
        Path(__file__).resolve().parents[1]
        / "tasks/io/chat_ui/resources_service_dialogs.js"
    ).read_text(encoding="utf-8")
    form_ui = (
        Path(__file__).resolve().parents[1]
        / "tasks/io/chat_ui/resources_service_login.js"
    ).read_text(encoding="utf-8")

    assert set(item["type"] for item in schema.values()) == {
        "string", "select", "integer", "boolean", "password",
    }
    assert "ptype === 'boolean'" in ui
    assert "ptype === 'select'" in ui
    assert "ptype === 'integer'" in ui
    assert "pdef.sensitive" in ui
    assert "type=\"password\"" in ui
    assert "_collectSchemaValues(schema)" in ui
    assert "action$('service_install'" in form_ui
    assert "action$('update_service'" in form_ui


def test_search_cli_runs_server_binary_with_isolated_scoped_keys(monkeypatch):
    service = _connected_service({
        "brave_api_key": "scoped-brave-secret",
        "providers": "brave",
        "timeout": 7,
    })
    captured = {}
    monkeypatch.setenv("BRAVE_API_KEY", "inherited-brave-secret")
    monkeypatch.setenv("SEARCH_KEYS_EXA", "inherited-exa-secret")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            "status": "success",
            "results": [],
            "answers": [],
            "metadata": {"elapsed_ms": 1},
        }), stderr="")

    monkeypatch.setattr("services.web_search_service.subprocess.run", fake_run)

    service.search("query with shell-like ; characters", count=4)

    assert captured["command"][0] == "/usr/local/bin/search"
    assert captured["command"][1:4] == ["search", "-q", "query with shell-like ; characters"]
    assert "--no-cache" in captured["command"]
    assert captured["env"]["SEARCH_LOG"] == "off"
    assert captured["env"]["SEARCH_KEYS_BRAVE"] == "scoped-brave-secret"
    assert "BRAVE_API_KEY" not in captured["env"]
    assert "SEARCH_KEYS_EXA" not in captured["env"]
    assert captured["env"]["XDG_CONFIG_HOME"].startswith("/tmp/pawflow-search-")
    assert captured["timeout"] == 12


def test_search_cli_error_redacts_scoped_key(monkeypatch):
    service = _connected_service({"brave_api_key": "do-not-leak-this"})

    monkeypatch.setattr(
        "services.web_search_service.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="bad key do-not-leak-this"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.search("test")

    assert "do-not-leak-this" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_search_cli_is_bundled_only_in_the_pawflow_server_image():
    root = Path(__file__).resolve().parents[1]
    server_dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    relay_dockerfile = (root / "docker/relay-dev/Dockerfile").read_text(encoding="utf-8")

    assert "search-cli-builder" in server_dockerfile
    assert "target/release/search /usr/local/bin/search" in server_dockerfile
    assert "search-cli-builder" not in relay_dockerfile
    assert "target/release/search" not in relay_dockerfile
