"""ACP registry service actions: catalogue, prepare (package/binary job), status, update check."""

from __future__ import annotations

import json
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from core.acp import registry as reg
from tasks.ai.actions import _sf_acp_registry as mod
from tasks.ai.actions._sf_base import _UNHANDLED


class _FlowFile:
    def __init__(self):
        self._content = b""

    def get_attribute(self, _name):
        return ""

    def set_content(self, data):
        self._content = data

    def payload(self):
        return json.loads(self._content or b"{}")


def _run(action, body, user_id="user-1"):
    flowfile = _FlowFile()
    helpers = tuple(lambda *a, **k: "" for _ in range(6))
    result = mod._handle_sf_acp_registry(None, action, body, None, user_id, flowfile, helpers)
    return result, flowfile


@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    codex = reg.parse_entry({
        "id": "codex-acp", "name": "Codex", "version": "1.8.0",
        "description": "ACP adapter for OpenAI's coding assistant", "license": "Apache-2.0",
        "distribution": {"npx": {"package": "@agentclientprotocol/codex-acp@1.8.0"}},
    }, matrix={"codex-acp": {"authMethods": ["agent"], "capabilities": {"loadSession": True}}})
    goose = reg.parse_entry({
        "id": "goose", "name": "goose", "version": "1.49.0", "description": "agent",
        "license": "Apache-2.0",
        "distribution": {"binary": {"linux-x86_64": {
            "archive": "https://github.com/block/goose/releases/download/v1.49.0/goose.tar.gz",
            "cmd": "./goose", "args": ["acp"]}}},
    })
    fast = reg.parse_entry({
        "id": "fast-agent", "name": "fast-agent", "version": "0.10.1", "description": "x",
        "distribution": {"uvx": {"package": "fast-agent-acp==0.10.1"}},
    }, quarantine={"fast-agent": "Timeout after 120s"})
    cat = reg.Catalogue(entries=(codex, goose, fast), registry_version="1.0.0",
                        fetched_at=1.0, stale=False)
    monkeypatch.setattr(reg, "load_catalogue", lambda *a, **k: cat)
    monkeypatch.setattr(reg, "host_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(reg, "agents_dir", lambda: tmp_path / "agents")
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    return cat


def test_unrelated_actions_are_unhandled():
    result, _ = _run("gemini_server_login", {})
    assert result is _UNHANDLED


def test_catalogue_lists_entries_platform_and_runners(catalogue):
    _, flowfile = _run("acp_registry_catalogue", {})
    payload = flowfile.payload()
    assert payload["ok"] is True and payload["registry_version"] == "1.0.0"
    assert [e["id"] for e in payload["entries"]] == ["codex-acp", "goose", "fast-agent"]
    assert payload["platform"] == "linux-x86_64"
    assert payload["runners"] == {"npx": True, "uvx": False}
    assert payload["entries"][2]["quarantined"] is True
    assert payload["entries"][1]["platforms"] == ["linux-x86_64"]
    assert "archive" not in json.dumps(payload), "no download URLs reach the UI"


def test_catalogue_unavailable_is_an_error_not_an_empty_list(catalogue, monkeypatch):
    def _boom(*a, **k):
        raise reg.RegistryUnavailable("cannot fetch registry and no cached copy exists")
    monkeypatch.setattr(reg, "load_catalogue", _boom)
    _, flowfile = _run("acp_registry_catalogue", {})
    assert "no cached copy" in flowfile.payload()["error"]


def test_prepare_package_returns_form_values(catalogue, tmp_path):
    _, flowfile = _run("acp_registry_prepare", {"agent_id": "codex-acp", "distribution": "npx"})
    payload = flowfile.payload()
    assert payload["ok"] is True and payload["status"] == "ready"
    config = payload["config"]
    assert config["provider"] == "acp" and config["auth_mode"] == "none"
    assert config["acp_command"] == "/usr/bin/npx"
    assert json.loads(config["acp_args"]) == ["--yes", "@agentclientprotocol/codex-acp@1.8.0"]
    assert json.loads(config["acp_env"]) == {}
    record = json.loads(config["acp_registry"])
    assert record["id"] == "codex-acp" and record["version"] == "1.8.0"
    assert record["distribution"] == "npx" and record["auth_types"] == ["agent"]
    assert config["acp_auto_auth_single_method"] is True and config["acp_load_session"] is True
    expected_cwd = tmp_path / "agents" / "codex-acp" / "workspace"
    assert config["acp_cwd"] == str(expected_cwd) and expected_cwd.is_dir()


def test_prepare_keeps_the_cwd_the_form_already_has(catalogue, tmp_path):
    _, flowfile = _run("acp_registry_prepare", {
        "agent_id": "codex-acp", "distribution": "npx", "cwd": str(tmp_path)})
    assert flowfile.payload()["config"]["acp_cwd"] == str(tmp_path)


@pytest.mark.parametrize("body,fragment", [
    ({}, "agent_id and distribution are required"),
    ({"agent_id": "nope", "distribution": "npx"}, "unknown ACP registry agent"),
    ({"agent_id": "goose", "distribution": "npx"}, "no npx distribution"),
    ({"agent_id": "fast-agent", "distribution": "uvx"}, "quarantined"),
    ({"agent_id": "codex-acp", "distribution": "deb"}, "unknown distribution"),
    ({"agent_id": "codex-acp", "distribution": "binary"}, "has no binary for linux-x86_64"),
])
def test_prepare_refusals(catalogue, body, fragment):
    _, flowfile = _run("acp_registry_prepare", body)
    assert fragment in flowfile.payload()["error"]


def test_prepare_reports_a_missing_runner_by_name(catalogue, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    _, flowfile = _run("acp_registry_prepare", {"agent_id": "codex-acp", "distribution": "npx"})
    assert "needs 'npx'" in flowfile.payload()["error"]


class _SyncThread:
    def __init__(self, target=None, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_prepare_binary_runs_a_job_and_status_reports_it(catalogue, monkeypatch, tmp_path):
    monkeypatch.setattr(mod.threading, "Thread", _SyncThread)
    directory = tmp_path / "agents" / "goose" / "1.49.0" / "linux-x86_64"
    done = reg.Materialised(directory=directory, command=directory / "goose", args=("acp",),
                            env={}, archive_sha256="ab" * 32, verified=False)
    seen = {}

    def _materialise(entry, platform, **kwargs):
        seen["entry"], seen["platform"] = entry.id, platform
        return done

    monkeypatch.setattr(reg, "materialise_binary", _materialise)
    _, flowfile = _run("acp_registry_prepare", {
        "agent_id": "goose", "distribution": "binary", "cwd": str(tmp_path)})
    payload = flowfile.payload()
    assert payload["status"] == "pending" and payload["job_id"]
    assert seen == {"entry": "goose", "platform": "linux-x86_64"}

    _, status = _run("acp_registry_prepare_status", {"job_id": payload["job_id"]})
    state = status.payload()
    assert state["status"] == "ready" and state["verified"] is False
    config = state["config"]
    assert config["acp_command"] == str(directory / "goose")
    assert json.loads(config["acp_args"]) == ["acp"]
    record = json.loads(config["acp_registry"])
    assert record["platform"] == "linux-x86_64" and record["archive_sha256"] == "ab" * 32
    assert record["archive_verified"] is False

    _, unknown = _run("acp_registry_prepare_status", {"job_id": "nope"})
    assert "unknown ACP registry job" in unknown.payload()["error"]


def test_prepare_binary_job_failure_is_reported_to_the_poller(catalogue, monkeypatch):
    monkeypatch.setattr(mod.threading, "Thread", _SyncThread)

    def _boom(entry, platform, **kwargs):
        raise reg.RegistryError("goose archive digest mismatch")

    monkeypatch.setattr(reg, "materialise_binary", _boom)
    _, flowfile = _run("acp_registry_prepare", {"agent_id": "goose", "distribution": "binary"})
    job_id = flowfile.payload()["job_id"]
    _, status = _run("acp_registry_prepare_status", {"job_id": job_id})
    assert status.payload()["status"] == "error"
    assert "digest mismatch" in status.payload()["error"]


def test_job_table_is_bounded(catalogue, monkeypatch):
    monkeypatch.setattr(mod, "_JOBS", {})
    for index in range(mod._MAX_JOBS + 5):
        mod._remember_job(f"job-{index}", {"status": "pending"})
    assert len(mod._JOBS) == mod._MAX_JOBS
    assert "job-0" not in mod._JOBS and f"job-{mod._MAX_JOBS + 4}" in mod._JOBS


def test_check_update_reads_the_pinned_record_from_the_service(catalogue, monkeypatch):
    sdef = types.SimpleNamespace(config={
        "provider": "acp",
        "acp_registry": json.dumps({"id": "codex-acp", "version": "1.7.0", "distribution": "npx"}),
    })
    monkeypatch.setattr(mod, "_resolve_service_definition_for_action", lambda *a, **k: sdef)
    _, flowfile = _run("acp_registry_check_update", {"service_id": "svc-codex"})
    payload = flowfile.payload()
    assert payload["ok"] is True and payload["update_available"] is True
    assert payload["pinned"] == "1.7.0" and payload["latest"] == "1.8.0"
    assert "Re-import to upgrade" in payload["message"]
    assert json.loads(sdef.config["acp_registry"])["version"] == "1.7.0", "never upgraded in place"

    sdef.config["acp_registry"] = json.dumps({"id": "codex-acp", "version": "1.8.0"})
    _, flowfile = _run("acp_registry_check_update", {"service_id": "svc-codex"})
    assert "latest registry version" in flowfile.payload()["message"]

    sdef.config["acp_registry"] = ""
    _, flowfile = _run("acp_registry_check_update", {"service_id": "svc-codex"})
    assert "not imported" in flowfile.payload()["error"]


def test_check_update_needs_a_resolvable_service(catalogue, monkeypatch):
    _, flowfile = _run("acp_registry_check_update", {})
    assert flowfile.payload()["error"] == "Missing service_id"
    monkeypatch.setattr(mod, "_resolve_service_definition_for_action", lambda *a, **k: None)
    _, flowfile = _run("acp_registry_check_update", {"service_id": "ghost"})
    assert "not found" in flowfile.payload()["error"]


def test_service_form_declares_the_registry_actions():
    from services.llm_connection import LLMConnectionService

    actions = LLMConnectionService({"provider": "antigravity-acp", "auth_mode": "none"}).get_service_actions()
    by_id = {item["id"]: item for item in actions}
    importer = by_id["acp_registry_import"]
    assert importer["when"] == {"provider": ["acp"]}
    assert importer["server_action"] == "acp_registry_catalogue"
    assert importer["flow"] == "acp_registry_import" and importer["before_install"] is True
    checker = by_id["acp_registry_check_update"]
    assert checker["server_action"] == "acp_registry_check_update" and checker["flow"] == "simple"


def test_service_form_has_the_registry_record_field_for_acp_only():
    from services.llm_connection import LLMConnectionService

    service = LLMConnectionService({"provider": "antigravity-acp", "auth_mode": "none"})
    field = service.get_parameter_schema()["acp_registry"]
    assert field["type"] == "string" and field["multiline"] is True
    visibility = {}
    for rule in service.get_parameter_rules():
        providers = (rule.get("when") or {}).get("provider")
        if providers and "acp_registry" in rule["set"]:
            for provider in providers:
                visibility[provider] = rule["set"]["acp_registry"]["visible"]
    assert visibility["acp"] is True
    assert all(v is False for p, v in visibility.items() if p != "acp")


def test_service_flow_routes_the_registry_actions():
    from tasks.ai.actions import service_flow

    source = Path(service_flow.__file__).read_text(encoding="utf-8")
    assert "_handle_sf_acp_registry" in source
    assert source.index("_handle_sf_k9, _handle_sf_acp_registry") > 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_registry_import_ui_behaviour():
    completed = subprocess.run(
        ["node", "tests/js/acp_registry_import_spec.js"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "3 passing" in completed.stdout


def test_registry_import_ui_is_loaded_on_install_and_translated():
    from tasks.io.serve_chat_ui import _JS_MODULES

    module = "resources_service_acp_registry.js"
    assert module in _JS_MODULES
    assert _JS_MODULES.index(module) < _JS_MODULES.index("resources_service_login.js")
    login = Path("tasks/io/chat_ui/resources_service_login.js").read_text(encoding="utf-8")
    assert "flow === 'acp_registry_import'" in login
    assert "_renderServiceActions(installActions, '', '')" in login
    keys = {
        "acpRegistryApplied", "acpRegistryAuth", "acpRegistryCheckUpdates",
        "acpRegistryDistribution", "acpRegistryImport", "acpRegistryLicense",
        "acpRegistryPreparing", "acpRegistryQuarantined", "acpRegistryTitle",
    }
    for language in ("en", "fr", "es"):
        catalogue = json.loads(
            Path(f"tasks/io/chat_ui/i18n/{language}.json").read_text(encoding="utf-8"))
        assert keys <= set(catalogue), language
