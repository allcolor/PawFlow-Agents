"""Phase-4 tests for the generic PFP semantic browser transport."""

from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import threading
import time
from uuid import uuid4
from pathlib import Path

import pytest

from core.conversation_event_bus import ConversationEventBus
from core.audit import AuditLog


@pytest.fixture(autouse=True)
def _reset_semantic_state():
    AuditLog.reset()
    ConversationEventBus.reset()
    from core.semantic_browser_bridge import SemanticBrowserBridge
    SemanticBrowserBridge.reset()
    yield
    SemanticBrowserBridge.reset()
    ConversationEventBus.reset()
    AuditLog.reset()


def _register(bridge, *, tab="tab-a", user="alice", conv="conv-1",
              packages=("example.semantic",), active=True):
    return bridge.register_tab(
        user_id=user,
        conversation_id=conv,
        tab_id=tab,
        bus_id="__ui__:" + tab,
        packages=list(packages),
        active=active,
    )


def test_semantic_bridge_correlates_one_authorized_tab():
    from core.semantic_browser_bridge import SemanticBrowserBridge

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    writer = ConversationEventBus.instance().subscribe(
        "__ui__:tab-a", replay=False, client_id="test")

    result = {}

    def invoke():
        result["value"] = bridge.call(
            user_id="alice",
            conversation_id="conv-1",
            caller={"package": "example.tool", "object_id": "tool:semantic"},
            grant={
                "package": "example.semantic",
                "operations": ["list", "get", "invoke"],
                "nodes": ["*"],
            },
            operation="invoke",
            arguments={
                "package": "example.semantic",
                "node": "example.semantic:stage.test",
                "action": "select",
                "arguments": {"name": "luna"},
            },
            timeout=1.0,
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    chunk = next(writer.iterate(timeout=1.0))
    assert b"event: pfp_semantic_request" in chunk
    payload = json.loads(chunk.split(b"data: ", 1)[1])
    assert payload["operation"] == "invoke"
    assert payload["caller"]["package"] == "example.tool"
    assert payload["target_package"] == "example.semantic"

    assert bridge.complete(
        user_id="alice",
        conversation_id="conv-1",
        tab_id="tab-a",
        request_id=payload["request_id"],
        result={"selected": "luna"},
    ) is True
    thread.join(2)
    assert result["value"] == {"selected": "luna"}
    audit = AuditLog.get_instance().query(action="pfp.semantic.*")
    assert [row["action"] for row in audit] == [
        "pfp.semantic.result", "pfp.semantic.request"]
    assert audit[0]["details"]["status"] == "success"
    assert audit[0]["details"]["target_package"] == "example.semantic"
    assert "arguments" not in audit[0]["details"]
    assert "result" not in audit[0]["details"]


def test_semantic_bridge_rejects_cross_user_and_cross_conversation_results():
    from core.semantic_browser_bridge import SemanticBrowserBridge, SemanticBrowserError

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    from core.semantic_browser_bridge import _PendingRequest
    pending_id = uuid4().hex
    bridge._pending[pending_id] = _PendingRequest(
        user_id="alice", conversation_id="conv-1", tab_id="tab-a")

    with pytest.raises(SemanticBrowserError, match="context mismatch"):
        bridge.complete(
            user_id="bob", conversation_id="conv-1", tab_id="tab-a",
            request_id=pending_id, result={})
    with pytest.raises(SemanticBrowserError, match="context mismatch"):
        bridge.complete(
            user_id="alice", conversation_id="conv-2", tab_id="tab-a",
            request_id=pending_id, result={})


def test_semantic_bridge_reports_unavailable_stale_and_ambiguous_tabs():
    from core.semantic_browser_bridge import SemanticBrowserBridge, SemanticBrowserError

    bridge = SemanticBrowserBridge.instance()
    with pytest.raises(SemanticBrowserError, match="no eligible browser tab"):
        bridge.select_tab("alice", "conv-1", "example.semantic")

    _register(bridge, tab="tab-a", active=True)
    _register(bridge, tab="tab-b", active=True)
    with pytest.raises(SemanticBrowserError, match="ambiguous"):
        bridge.select_tab("alice", "conv-1", "example.semantic")

    bridge.unregister_tab(
        user_id="alice", conversation_id="conv-1", tab_id="tab-b")
    assert bridge.select_tab(
        "alice", "conv-1", "example.semantic")["tab_id"] == "tab-a"

    bridge._tabs["tab-a"]["updated_at"] = time.monotonic() - 1000
    with pytest.raises(SemanticBrowserError, match="no eligible browser tab"):
        bridge.select_tab("alice", "conv-1", "example.semantic")


def test_semantic_tab_bus_is_bound_to_registered_user():
    from core.semantic_browser_bridge import SemanticBrowserBridge

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    assert bridge.authorize_bus("alice", "__ui__:tab-a") is True
    assert bridge.authorize_bus("bob", "__ui__:tab-a") is False
    assert bridge.authorize_bus("bob", "__ui__:unknown") is True
    with pytest.raises(Exception, match="another user"):
        _register(bridge, user="bob")


def test_semantic_bridge_disconnect_fails_pending_call():
    from core.semantic_browser_bridge import SemanticBrowserBridge, SemanticBrowserError

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    outcome = {}

    def invoke():
        try:
            bridge.call(
                user_id="alice", conversation_id="conv-1",
                caller={"package": "example.tool", "object_id": "tool:semantic"},
                grant={"package": "example.semantic",
                       "operations": ["list"], "nodes": ["*"]},
                operation="list",
                arguments={"package": "example.semantic"},
                timeout=2.0,
            )
        except Exception as exc:  # test captures the exact public error below
            outcome["error"] = exc

    thread = threading.Thread(target=invoke)
    thread.start()
    deadline = time.time() + 1
    while not bridge.pending_count() and time.time() < deadline:
        time.sleep(0.005)
    bridge.unregister_tab(
        user_id="alice", conversation_id="conv-1", tab_id="tab-a")
    thread.join(2)
    assert isinstance(outcome.get("error"), SemanticBrowserError)
    assert "disconnected" in str(outcome["error"])


def test_semantic_bridge_timeout_is_bounded_and_audited():
    from core.semantic_browser_bridge import SemanticBrowserBridge, SemanticBrowserError

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    with pytest.raises(SemanticBrowserError, match="timed out"):
        bridge.call(
            user_id="alice", conversation_id="conv-1",
            caller={"package": "example.tool", "object_id": "tool:semantic"},
            grant={"package": "example.semantic",
                   "operations": ["list"], "nodes": ["*"]},
            operation="list",
            arguments={"package": "example.semantic"},
            timeout=0.01,
        )
    assert bridge.pending_count() == 0
    audit = AuditLog.get_instance().query(action="pfp.semantic.timeout")
    assert len(audit) == 1
    assert audit[0]["details"]["status"] == "timeout"


def test_semantic_bridge_bounds_request_and_result_json():
    from core.semantic_browser_bridge import SemanticBrowserBridge, SemanticBrowserError

    bridge = SemanticBrowserBridge.instance()
    _register(bridge)
    with pytest.raises(SemanticBrowserError, match="request is too large"):
        bridge.call(
            user_id="alice", conversation_id="conv-1",
            caller={"package": "example.tool", "object_id": "tool:semantic"},
            grant={"package": "example.semantic",
                   "operations": ["invoke"], "nodes": ["*"]},
            operation="invoke",
            arguments={"package": "example.semantic", "node": "x",
                       "action": "x", "arguments": {"blob": "x" * 70000}},
            timeout=0.01,
        )

    from core.semantic_browser_bridge import _PendingRequest
    pending_id = uuid4().hex
    bridge._pending[pending_id] = _PendingRequest(
        user_id="alice", conversation_id="conv-1", tab_id="tab-a")
    with pytest.raises(SemanticBrowserError, match="result is too large"):
        bridge.complete(
            user_id="alice", conversation_id="conv-1", tab_id="tab-a",
            request_id=pending_id, result={"blob": "x" * 70000})


def test_runtime_host_browser_call_is_permission_gated(monkeypatch):
    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError

    seen = {}

    class _Bridge:
        def call(self, **kwargs):
            seen.update(kwargs)
            return {"nodes": []}

    monkeypatch.setattr(
        "core.semantic_browser_bridge.SemanticBrowserBridge.instance",
        lambda: _Bridge())
    monkeypatch.setattr(
        "core.pfp_capabilities.PackageCapabilityBroker._require_installed_package",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "core.tool_mcp_filters._ui_extensions_globally_disabled",
        lambda: False)
    monkeypatch.setattr(
        "core.tool_mcp_filters.is_extension_enabled",
        lambda *_args, **_kwargs: True)

    runtime = {
        "package": "example.tool",
        "object_id": "tool:semantic",
        "permissions": {
            "browser": {
                "semantic": [{
                    "package": "example.semantic",
                    "operations": ["list"],
                    "nodes": ["*"],
                }]
            }
        },
    }
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice", conversation_id="conv-1", caller_runtime=runtime)

    assert host.handle_host_call({
        "format": pfp_runtime.HOST_CALL_FORMAT,
        "kind": "browser",
        "target": "semantic",
        "operation": "list",
        "arguments": {"package": "example.semantic"},
    }) == {"nodes": []}
    assert seen["grant"]["package"] == "example.semantic"

    runtime["permissions"] = {}
    with pytest.raises(PackageCapabilityError, match="not allowed"):
        host.handle_host_call({
            "format": pfp_runtime.HOST_CALL_FORMAT,
            "kind": "browser",
            "target": "semantic",
            "operation": "list",
            "arguments": {"package": "example.semantic"},
        })


def test_runtime_invocation_preserves_browser_permissions(tmp_path):
    from core import pfp_runtime

    entrypoint = tmp_path / "main.py"
    entrypoint.write_text("pass\n", encoding="utf-8")
    permissions = {
        "browser": {
            "semantic": [{
                "package": "example.semantic",
                "operations": ["list"],
                "nodes": ["*"],
            }]
        }
    }
    request = pfp_runtime.build_tool_invocation(
        {
            "package": "example.tool",
            "version": "1.0.0",
            "object_id": "tool:semantic",
            "runtime": "python",
            "runner": "python",
            "content_dir": str(tmp_path),
            "entrypoint": "main.py",
            "permissions": permissions,
        },
        {},
        {},
        {
            "user_id": "alice",
            "conversation_id": "conv-1",
            "agent_name": "assistant",
        },
    )
    assert request["package"]["permissions"] == permissions
    host = pfp_runtime.runtime_host_from_invocation(request)
    assert host.caller_runtime["permissions"] == permissions


def test_browser_permission_manifest_validation_and_dependency():
    from core.pfp_package._pp_base import PfpError
    from core.pfp_package._pp_mod4 import (
        _browser_semantic_grants, _declared_package_dependencies)

    obj = {
        "permissions": {
            "browser": {
                "semantic": [{
                    "package": "example.semantic",
                    "operations": ["list", "invoke"],
                    "nodes": ["example.semantic:stage.test"],
                }]
            }
        }
    }
    grants = _browser_semantic_grants(obj)
    assert grants[0]["package"] == "example.semantic"
    deps = _declared_package_dependencies(
        {"dependencies": []}, obj)
    assert any(
        dep["package"] == "example.semantic" for dep in deps)

    obj["permissions"]["browser"]["semantic"][0]["operations"] = ["execute"]
    with pytest.raises(PfpError, match="operations"):
        _browser_semantic_grants(obj)


def test_semantic_sdk_facade_emits_browser_host_calls(monkeypatch, tmp_path):
    sdk_path = (
        Path(__file__).resolve().parents[1]
        / "docker" / "pawflow_sdk" / "pawflow.py")
    spec = importlib.util.spec_from_file_location(
        "pawflow_semantic_sdk_under_test", sdk_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(
        module.pfp, "_host_call",
        lambda kind, target, **kwargs: calls.append(
            (kind, target, kwargs)) or {"ok": True})

    assert module.pfp.browser.semantic.list(
        "example.semantic") == {"ok": True}
    module.pfp.browser.semantic.get(
        "example.semantic", "example.semantic:stage.test")
    module.pfp.browser.semantic.invoke(
        "example.semantic", "example.semantic:stage.test",
        "select", {"name": "luna"})
    assert [row[0:2] for row in calls] == [
        ("browser", "semantic"),
        ("browser", "semantic"),
        ("browser", "semantic"),
    ]
    assert calls[-1][2]["operation"] == "invoke"
    assert calls[-1][2]["arguments"]["arguments"] == {"name": "luna"}


def test_semantic_example_builds_and_has_an_installable_dry_run(
        monkeypatch, tmp_path):
    from core import pfp_package
    import core.paths as paths

    monkeypatch.setattr(
        paths, "REPOSITORY_DIR", tmp_path / "repository")
    source = (
        Path(__file__).resolve().parents[1]
        / "docs" / "examples" / "pfp" / "semantic_ui_tool.pfpdir")
    package_dir = tmp_path / "semantic_ui_tool.pfpdir"
    shutil.copytree(source, package_dir)
    keypair = pfp_package.create_signing_key()
    manifest_path = package_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["developer"]["public_key"] = keypair["public_key"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    built = pfp_package.build_pfp(
        str(package_dir), private_key=keypair["private_key"])
    result = pfp_package.install_pfp(
        built["path"], user_id="alice", dry_run=True)
    assert result["ok"] is True
    assert {row["id"] for row in result["installed"]} == {
        "ui_extension:semantic-demo",
        "tool:semantic-demo",
    }


def test_semantic_ui_action_registers_only_installed_enabled_packages(
        monkeypatch):
    from core import FlowFile
    from core.semantic_browser_bridge import SemanticBrowserBridge
    from tasks.ai.actions.pfp_semantic import _handle_pfp_semantic

    monkeypatch.setattr(
        "core.conversation_access.require_read", lambda *_args: None)
    monkeypatch.setattr(
        "core.pfp_package.list_installed_ui_extensions",
        lambda **_kwargs: [{"package": "example.semantic"}])
    monkeypatch.setattr(
        "core.tool_mcp_filters._ui_extensions_globally_disabled",
        lambda: False)
    monkeypatch.setattr(
        "core.tool_mcp_filters.is_extension_enabled",
        lambda *_args: True)
    flowfile = FlowFile(content=b"")
    result = _handle_pfp_semantic(
        None, "pfp_semantic_tab_register", {
            "conversation_id": "conv-1",
            "tab_id": "tab-a",
            "bus_id": "__ui__:tab-a",
            "packages": ["example.semantic", "forged.package"],
            "active": True,
        }, None, "alice", flowfile)
    assert result == [flowfile]
    payload = json.loads(flowfile.get_content())
    assert payload["packages"] == ["example.semantic"]
    assert SemanticBrowserBridge.instance().select_tab(
        "alice", "conv-1", "example.semantic")["tab_id"] == "tab-a"


def test_semantic_ui_action_rejects_disabled_extension(monkeypatch):
    from core import FlowFile
    from tasks.ai.actions.pfp_semantic import _handle_pfp_semantic

    monkeypatch.setattr(
        "core.conversation_access.require_read", lambda *_args: None)
    monkeypatch.setattr(
        "core.tool_mcp_filters._ui_extensions_globally_disabled",
        lambda: True)
    flowfile = FlowFile(content=b"")
    _handle_pfp_semantic(
        None, "pfp_semantic_tab_register", {
            "conversation_id": "conv-1",
            "tab_id": "tab-a",
            "bus_id": "__ui__:tab-a",
            "packages": ["example.semantic"],
        }, None, "alice", flowfile)
    assert flowfile.get_attribute("http.response.status") == "409"
    assert "disabled" in json.loads(flowfile.get_content())["error"]


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not available to run the JS suite")
def test_semantic_browser_runtime_behaviour():
    proc = subprocess.run(
        ["node", "tests/js/pfp_semantic_runtime_spec.js"],
        capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, (
        "JS suite failed:\n" + proc.stdout + proc.stderr)
