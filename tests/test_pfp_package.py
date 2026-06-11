import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from core import pfp_package
from core import pfp_registry
from tasks.ai.actions.command_dispatch import _parse_command


class _Response:
    def __init__(self, content, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


def _write_package_dir(root, keypair, version="1.0.0", skill_body="Use the package skill safely.",
                       package_id="community.wavespeed", skill_name="pkg-skill",
                       dependencies=None, tool_allowed_tools=None,
                       include_service_provider=False, include_flow_task=False,
                       include_agent_hook=False,
                       tool_runner="python", service_runner="python", flow_task_runner="python",
                       tool_secrets=None):
    pkg = root / "wavespeed-provider.pfpdir"
    skill_dir = pkg / "content" / "skills" / skill_name
    agent_dir = pkg / "content" / "agents"
    tool_dir = pkg / "content" / "tools" / "reader"
    service_provider_dir = pkg / "content" / "service-providers" / "image"
    flow_task_dir = pkg / "content" / "flow-tasks" / "image-resize"
    agent_hook_dir = pkg / "content" / "hooks"
    skill_dir.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    if include_service_provider:
        service_provider_dir.mkdir(parents=True)
    if include_flow_task:
        flow_task_dir.mkdir(parents=True)
    if include_agent_hook:
        agent_hook_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: Package skill\n"
        "---\n"
        f"{skill_body}\n",
        encoding="utf-8",
    )
    (agent_dir / "helper.json").write_text(json.dumps({
        "prompt": "You are a helper agent.",
        "description": "Helper from package",
    }), encoding="utf-8")
    (tool_dir / "main.py").write_text("print('not executable at install')\n", encoding="utf-8")
    if include_service_provider:
        (service_provider_dir / "provider.py").write_text(
            "raise RuntimeError('not executable at install')\n", encoding="utf-8")
    if include_flow_task:
        (flow_task_dir / "task.py").write_text(
            "raise RuntimeError('not executable at install')\n", encoding="utf-8")
    if include_agent_hook:
        (agent_hook_dir / "guard.py").write_text(
            "from pawflow import pfp\n"
            "pfp.result({'decision': 'allow', 'payload': pfp.payload.get('event', {}).get('payload', {})})\n",
            encoding="utf-8")
    manifest = {
        "format": "pawflow.package.v1",
        "package": package_id,
        "version": version,
        "description": "WaveSpeed-style provider package",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "origin": {"source": "local-test"},
        "objects": [
            {
                "id": f"skill:{skill_name}",
                "type": "skill",
                "name": skill_name,
                "path": f"content/skills/{skill_name}/SKILL.md",
            },
            {
                "id": "agent:helper",
                "type": "agent",
                "name": "helper",
                "path": "content/agents/helper.json",
            },
            {
                "id": "tool:reader",
                "type": "tool",
                "name": "reader",
                "path": "content/tools/reader/main.py",
                "allowed_tools": tool_allowed_tools or [{"name": "read"}],
            },
        ],
    }
    if tool_runner:
        manifest["objects"][2]["runner"] = tool_runner
    if tool_secrets:
        manifest["objects"][2]["secrets"] = tool_secrets
    if include_service_provider:
        service_object = {
            "id": "service_provider:image",
            "type": "service_provider",
            "name": "wavespeed-image-provider",
            "service_id": "wavespeed-image-provider",
            "path": "content/service-providers/image/provider.py",
            "description": "Image provider from package",
            "provides": ["media.image_generation"],
            "operations": {"generate": {}},
            "allowed_tools": [{"name": "read"}],
        }
        if service_runner:
            service_object["runner"] = service_runner
        manifest["objects"].append(service_object)
    if include_flow_task:
        flow_task_object = {
            "id": "flow_task:resize-image",
            "type": "flow_task",
            "name": "packageResizeImage",
            "task_type": "packageResizeImage",
            "path": "content/flow-tasks/image-resize/task.py",
            "description": "Resize an image from a package task",
            "parameters": {
                "width": {"type": "integer", "required": True},
            },
            "allowed_tools": [{"name": "read"}],
        }
        if flow_task_runner:
            flow_task_object["runner"] = flow_task_runner
        manifest["objects"].append(flow_task_object)
    if include_agent_hook:
        manifest["objects"].append({
            "id": "agent_hook:guard",
            "type": "agent_hook",
            "name": "guard",
            "path": "content/hooks/guard.py",
            "runner": "python",
            "description": "Package hook",
            "events": ["pre_tool_call"],
            "allowed_tools": [{"name": "read"}],
        })
    if dependencies:
        manifest["dependencies"] = dependencies
    (pkg / "pfp.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pkg


def _reset_repo(tmp_path, monkeypatch):
    import core.paths as paths
    import core.package_review as package_review
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore
    from core.service_registry import ServiceRegistry
    from core import TaskFactory
    from tasks.ai.actions import agent_resource

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    ResourceStore.reset()
    ServiceRegistry.reset()
    TaskFactory._tasks.pop("packageResizeImage", None)
    agent_resource._FLOW_TEMPLATES_CACHE.clear()
    agent_resource._FLOW_TEMPLATES_REFRESHING.clear()

    class _ReviewLLM:
        def complete(self, **kwargs):
            class _Response:
                content = json.dumps({
                    "risk": "low",
                    "allowed": True,
                    "requires_human_review": False,
                    "findings": [],
                    "sanitized_summary": "ok",
                    "recommended_changes": [],
                })
            return _Response()

    monkeypatch.setattr(
        package_review,
        "_resolve_review_llm",
        lambda user_id, conversation_id: (_ReviewLLM(), None, "review_llm"),
    )


def test_pfp_build_inspect_and_selective_install(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)

    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    assert built["ok"] is True
    assert built["path"].endswith("community.wavespeed-1.0.0.pfp")

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    assert plan["verified"] is True
    assert plan["package"] == "community.wavespeed"
    tool_row = next(row for row in plan["objects"] if row["id"] == "tool:reader")
    assert tool_row["status"] == "new"
    assert tool_row["selected"] is True
    assert tool_row["hash"].startswith("sha256:")

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert result["ok"] is True
    assert [row["id"] for row in result["installed"]] == ["skill:pkg-skill"]

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("skill", "pkg-skill", "alice")
    assert stored is not None
    assert stored["installed_from"]["package"] == "community.wavespeed"

    listed = pfp_package.list_installed_packages(user_id="alice")
    assert listed["packages"][0]["package"] == "community.wavespeed"


def test_pfp_update_with_different_developer_key_is_rejected(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    # First install pins the original developer key.
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    first = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert first["ok"] is True

    # A v2 signed by a DIFFERENT key (registry hijack / MITM) must be refused.
    attacker = pfp_package.create_signing_key()
    assert attacker["public_key"] != keypair["public_key"]
    (tmp_path / "v2").mkdir()
    pkgdir2 = _write_package_dir(tmp_path / "v2", attacker, version="2.0.0")
    built2 = pfp_package.build_pfp(str(pkgdir2), private_key=attacker["private_key"])

    import pytest
    with pytest.raises(pfp_package.PfpError, match="[Dd]eveloper key mismatch"):
        pfp_package.install_pfp(
            built2["path"], user_id="alice", include=["skill:pkg-skill"])

    # force=True is the explicit operator override and is allowed.
    forced = pfp_package.install_pfp(
        built2["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert forced["ok"] is True


def test_pfp_update_with_same_developer_key_is_allowed(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["skill:pkg-skill"], force=True)

    # Same signer ships v2 — the pin matches, install proceeds.
    (tmp_path / "v2").mkdir()
    pkgdir2 = _write_package_dir(tmp_path / "v2", keypair, version="2.0.0")
    built2 = pfp_package.build_pfp(str(pkgdir2), private_key=keypair["private_key"])
    result = pfp_package.install_pfp(
        built2["path"], user_id="alice", include=["skill:pkg-skill"])
    assert result["ok"] is True


def test_pfp_inspect_blocks_invalid_agent_skill_name(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, skill_name="Bad_Skill")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")

    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:Bad_Skill")
    assert skill_row["status"] == "blocked"
    assert skill_row["reason"] == "invalid Agent Skill name"
    assert skill_row["selected"] is False


def test_pfp_inspect_blocks_skill_without_description(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    skill_md = pkgdir / "content" / "skills" / "pkg-skill" / "SKILL.md"
    skill_md.write_text(
        "---\nname: pkg-skill\n---\nUse the package skill safely.\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")

    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:pkg-skill")
    assert skill_row["status"] == "blocked"
    assert skill_row["reason"] == "SKILL.md frontmatter.description is required"
    assert skill_row["selected"] is False


def test_pfp_installs_agent_hook_runtime_resource(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_agent_hook=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["agent_hook:guard"], force=True)

    assert result["ok"] is True
    assert [row["id"] for row in result["installed"]] == ["agent_hook:guard"]
    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("agent_hook", "guard", "alice")
    assert stored is not None
    assert stored["events"] == ["pre_tool_call"]
    assert stored["description"] == "Package hook"
    assert stored["package_runtime"]["object_id"] == "agent_hook:guard"
    assert stored["package_runtime"]["allowed_tools"] == [{"name": "read"}]


def test_pfp_inspect_exposes_capability_summary_for_preinstall_review(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair,
        dependencies=[{"package": "community.base", "version": "1.0.0"}],
        tool_allowed_tools=[
            {"name": "read"},
            {"package": "community.base", "object": "tool:normalize"},
        ],
        tool_secrets=[{"name": "api_key", "env": "PROVIDER_API_KEY"}],
        include_service_provider=True,
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")

    assert "tool:reader" in plan["capabilities"]["runtime_objects"]
    assert "service_provider:image" in plan["capabilities"]["runtime_objects"]
    assert {"kind": "tool", "ref": "read", "package": "", "object": "", "name": "read"} in plan["capabilities"]["allowed_tools"]
    assert any(ref["ref"] == "community.base/tool:normalize"
               for ref in plan["capabilities"]["allowed_tools"])
    assert {"name": "api_key", "env": "PROVIDER_API_KEY", "required": True} in plan["capabilities"]["secrets"]
    assert {"package": "community.base", "version": "1.0.0"} in plan["capabilities"]["dependencies"]

    display = pfp_package.format_inspection_display(plan)
    assert "PFP community.wavespeed@1.0.0" in display
    assert "runtime objects: tool:reader, service_provider:image" in display
    assert "brokered tools: read, community.base/tool:normalize" in display
    assert "dependencies: community.base@1.0.0" in display
    assert "secrets: api_key->PROVIDER_API_KEY" in display


def test_pfp_inspect_service_provider_conflict_uses_service_id(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_service_provider=True)
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for obj in manifest["objects"]:
        if obj.get("id") == "service_provider:image":
            obj["name"] = "display-name"
            obj["service_id"] = "runtime-service-id"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    from core.service_registry import ServiceRegistry, SCOPE_USER
    import services.package_runtime_service  # noqa: F401
    ServiceRegistry.get_instance().install(
        SCOPE_USER, "alice", "runtime-service-id", "packageRuntime",
        config={"package_runtime": {"package": "other", "object_id": "service_provider:x"}, "installed_from": {}},
    )

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(item for item in plan["objects"] if item["id"] == "service_provider:image")
    assert row["name"] == "display-name"
    assert row["status"] == "conflict"


def test_pfp_inspect_exposes_package_size_without_size_cap(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    large_payload = b"x" * 600_000
    (pkgdir / "content" / "bin" / "linux-amd64").mkdir(parents=True)
    (pkgdir / "content" / "bin" / "linux-amd64" / "tail").write_bytes(large_payload)

    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")

    assert built["content_size"] >= len(large_payload)
    assert built["package_size"] > 0
    assert plan["content_size"] >= len(large_payload)
    assert plan["package_size"] == built["package_size"]
    assert plan["file_count"] >= 1
    display = pfp_package.format_inspection_display(plan)
    assert "Size:" in display
    assert "content" in display


def test_pfp_installs_tool_as_relay_runtime_proxy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)
    assert result["ok"] is True
    assert result["installed"][0]["resource_type"] == "tool"

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    assert stored["source"] == ""
    assert stored["installed_from"]["package"] == "community.wavespeed"
    assert stored["package_runtime"]["runner"] == "python"
    assert stored["package_runtime"]["allowed_tools"] == [{"name": "read"}]
    content_dir = Path(stored["package_runtime"]["content_dir"])
    assert (content_dir / stored["package_runtime"]["entrypoint"]).exists()
    assert (content_dir / "pfp.lock.json").exists()

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry
    registry = ToolRegistry()
    assert load_tools_into_registry(registry, "alice") == 1
    handler = registry.get("reader")
    assert handler is not None
    assert getattr(handler, "_is_pfp_tool", False) is True
    assert "PFP runtime requires user_id and conversation_id" in handler.execute({})

    removed = pfp_package.uninstall_pfp("community.wavespeed", user_id="alice", force=True)
    assert removed["ok"] is True
    assert not content_dir.exists()


def test_pfp_install_requires_secret_binding_for_required_secret(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, tool_secrets=[{
            "name": "api_key",
            "env": "WAVESPEED_API_KEY",
            "required": True,
        }])
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    row = next(item for item in plan["objects"] if item["id"] == "tool:reader")
    assert row["risk"] == "high"
    assert row["secrets"] == [{
        "name": "api_key",
        "env": "WAVESPEED_API_KEY",
        "required": True,
    }]

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    assert result["ok"] is True
    assert result["installed"] == []
    assert result["skipped"][-1] == {
        "id": "tool:reader",
        "reason": "missing_secret_binding",
        "missing_secrets": ["api_key"],
    }


def test_pfp_install_rejects_binding_to_missing_secret_key(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair,
        tool_secrets=[{"name": "api_key", "env": "WAVESPEED_API_KEY"}])
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True,
        secret_bindings={"api_key": "missing_key"})

    assert result["ok"] is True
    assert result["installed"] == []
    assert result["skipped"][-1] == {
        "id": "tool:reader",
        "reason": "unavailable_secret_binding",
        "missing_secret_keys": ["missing_key"],
    }


def test_pfp_tool_runner_stores_bound_secret_env(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair,
        tool_secrets=[{"name": "api_key", "env": "WAVESPEED_API_KEY"}])
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    from core.paths import user_secrets_path
    secrets_path = user_secrets_path("alice")
    ConfigStore.save_secrets(secrets_path, {"wavespeed_key": ConfigValue(value="sk-test")})

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True,
        secret_bindings={"api_key": "wavespeed_key"})
    assert result["installed"][0]["id"] == "tool:reader"

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    assert stored["package_runtime"]["runner"] == "python"
    assert stored["package_runtime"]["secret_bindings"] == {"api_key": "wavespeed_key"}
    assert stored["package_runtime"]["secrets"][0]["env"] == "WAVESPEED_API_KEY"


def test_pfp_install_command_parses_secret_bindings():
    parsed = _parse_command(
        "/pfp install ./pkg.pfp --include tool:reader --secret api_key=wavespeed_key",
        "conv1", "alice", "assistant",
    )

    assert parsed["action"] == "pfp_install"
    assert parsed["secret_bindings"] == {"api_key": "wavespeed_key"}


def test_pfp_dev_load_command_defaults_to_conversation_scope():
    parsed = _parse_command(
        "/pfp dev-load ./pkg.pfpdir --include service_provider:image --secret api_key=provider_key",
        "conv1", "alice", "assistant",
    )

    assert parsed["action"] == "pfp_dev_load"
    assert parsed["source_dir"] == "./pkg.pfpdir"
    assert parsed["scope"] == "conversation"
    assert parsed["include"] == ["service_provider:image"]
    assert parsed["secret_bindings"] == {"api_key": "provider_key"}


def test_pfp_dev_unload_command_defaults_to_conversation_scope():
    parsed = _parse_command(
        "/pfp dev-unload community.wavespeed",
        "conv1", "alice", "assistant",
    )

    assert parsed["action"] == "pfp_dev_unload"
    assert parsed["package"] == "community.wavespeed"
    assert parsed["scope"] == "conversation"


def test_pfp_tool_proxy_rejects_tampered_entrypoint(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    content_dir = Path(stored["package_runtime"]["content_dir"])
    (content_dir / stored["package_runtime"]["entrypoint"]).write_text(
        "print('tampered')\n", encoding="utf-8")

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry
    registry = ToolRegistry()
    assert load_tools_into_registry(registry, "alice") == 1
    result = registry.get("reader").execute({})

    assert "PFP runtime entrypoint hash mismatch" in result


def test_pfp_runtime_builds_tool_invocation_envelope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core import pfp_runtime
    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    request = pfp_runtime.build_tool_invocation(
        stored["package_runtime"], stored["installed_from"], {"path": "in.txt"})

    assert request["format"] == "pawflow.package.runtime.invoke.v1"
    assert request["kind"] == "tool"
    assert request["package"]["package"] == "community.wavespeed"
    assert request["package"]["object_id"] == "tool:reader"
    assert request["package"]["entrypoint"] == "content/tools/reader/main.py"
    assert request["package"]["runner"] == "python"
    assert request["package"]["allowed_tools"] == [{"name": "read"}]
    assert request["package"]["allowed_services"] == []
    assert request["payload"] == {"arguments": {"path": "in.txt"}}


def test_pfp_runtime_rejects_missing_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text("print('unused')\n", encoding="utf-8")

    from core import pfp_runtime
    try:
        pfp_runtime.invoke_tool({
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:hello",
            "content_dir": str(content_dir),
            "entrypoint": "main.py",
            "runtime": "python",
        }, {}, {}, {"user_id": "alice", "conversation_id": "conv1"})
    except pfp_runtime.PackageRuntimeError as exc:
        assert "unsupported PFP runtime runner" in str(exc)
    else:
        raise AssertionError("runtime objects must declare runner='python'")


def test_pfp_runtime_uses_relay_bridge_for_verified_tool_envelope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core import pfp_runtime
    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    calls = []

    def _invoke(self, request):
        calls.append(request)
        return {"format": pfp_runtime.RUNTIME_RESULT_FORMAT, "ok": True, "result": "bridge-result"}

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    result = pfp_runtime.invoke_tool(
        stored["package_runtime"], stored["installed_from"], {"path": "in.txt"},
        {"user_id": "alice", "conversation_id": "conv1"})

    assert result == "bridge-result"
    assert len(calls) == 1
    assert calls[0]["format"] == "pawflow.package.runtime.invoke.v1"
    assert calls[0]["kind"] == "tool"
    assert calls[0]["package"]["object_id"] == "tool:reader"
    assert calls[0]["payload"] == {"arguments": {"path": "in.txt"}}


def test_pfp_tool_proxy_passes_runtime_context(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core import pfp_runtime
    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry
    calls = []

    def _invoke(self, request):
        calls.append(request)
        return {"format": pfp_runtime.RUNTIME_RESULT_FORMAT, "ok": True, "result": "ok"}

    registry = ToolRegistry()
    load_tools_into_registry(registry, "alice", conversation_id="conv1")
    handler = registry.get("reader")
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    assert handler.execute({"path": "in.txt"}) == "ok"

    assert calls[0]["context"] == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "",
    }




def test_pfp_runtime_task_envelope_carries_flowfile_content(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime
    task_cls = TaskFactory.get("packageResizeImage")
    request = pfp_runtime.build_task_invocation(
        task_cls.PACKAGE_RUNTIME,
        task_cls.INSTALLED_FROM,
        {"width": 64},
        FlowFile(content=b"image-bytes", attributes={"mime.type": "image/png"}),
    )

    flowfile = request["payload"]["flowfile"]
    assert request["format"] == "pawflow.package.runtime.invoke.v1"
    assert request["kind"] == "flow_task"
    assert flowfile["attributes"] == {"mime.type": "image/png"}
    assert flowfile["content_size"] == len(b"image-bytes")
    assert flowfile["_content_bytes"] == b"image-bytes"
    assert "content_b64" not in flowfile

    class _Relay:
        def __init__(self):
            self.files = {}

        def mkdir(self, path):
            pass

        def write_file(self, path, content):
            self.files[path] = content

    relay = _Relay()
    staged = pfp_runtime.RelayPackageRuntimeBridge()._relay_request(
        request, relay, ".pawflow/pfp/root")
    staged_flowfile = staged["payload"]["flowfile"]
    assert "_content_bytes" not in staged_flowfile
    assert staged_flowfile["content_path"].startswith(".pawflow/flowfiles/input-")
    assert relay.files[f".pawflow/pfp/root/{staged_flowfile['content_path']}"] == b"image-bytes"


def test_pfp_runtime_task_stages_spilled_flowfile_without_materializing(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime
    from core.stream import SPILL_THRESHOLD
    task_cls = TaskFactory.get("packageResizeImage")
    source_flowfile = FlowFile(content=b"x" * (SPILL_THRESHOLD + 1))
    request = pfp_runtime.build_task_invocation(
        task_cls.PACKAGE_RUNTIME,
        task_cls.INSTALLED_FROM,
        {"width": 64},
        source_flowfile,
    )

    flowfile = request["payload"]["flowfile"]
    assert flowfile["content_size"] == SPILL_THRESHOLD + 1
    assert "_content_path" in flowfile
    assert "_content_bytes" not in flowfile

    class _Relay:
        def __init__(self):
            self.calls = []

        def mkdir(self, path):
            pass

        def write_file(self, path, content):
            raise AssertionError("spilled FlowFile should use chunked relay writes")

        def _request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return {}

    relay = _Relay()
    staged = pfp_runtime.RelayPackageRuntimeBridge()._relay_request(
        request, relay, ".pawflow/pfp/root")
    staged_flowfile = staged["payload"]["flowfile"]
    assert "_content_path" not in staged_flowfile
    assert staged_flowfile["content_path"].startswith(".pawflow/flowfiles/input-")
    assert relay.calls
    assert relay.calls[-1][0] == "write_file_chunked"
    assert relay.calls[-1][2]["done"] is True


def test_pfp_flow_task_is_visible_to_admin_flow_builder(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from tasks.io.admin_editor_actions import (
        _admin_get_task_schema,
        _admin_list_task_types,
        _admin_validate_flow,
    )

    catalog = _admin_list_task_types({}, None, None, None, None)
    entry = next(item for item in catalog if item["type"] == "packageResizeImage")
    assert entry["name"] == "packageResizeImage"
    assert entry["description"] == "Resize an image from a package task"
    assert entry["icon"] == "package"
    assert entry["category"] == "Plugins"

    schema = _admin_get_task_schema(
        {"task_type": "packageResizeImage"}, None, None, None, None)
    assert schema == {
        "type": "packageResizeImage",
        "schema": {
            "width": {"type": "integer", "required": True},
            "relay": {
                "type": "string",
                "required": True,
                "description": "Filesystem relay service id used to execute this package task.",
            },
        },
    }

    validation = _admin_validate_flow({"flow": {
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64, "relay": "relay1"}}},
        "relations": [],
        "entries": ["resize"],
        "exits": ["resize"],
    }}, None, None, None, None)
    assert validation == {"errors": [], "warnings": []}


def test_pfp_runtime_task_result_rebuilds_flowfiles(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime
    task_cls = TaskFactory.get("packageResizeImage")

    def _invoke(self, request):
        assert request["context"]["user_id"] == "alice"
        assert request["context"]["conversation_id"] == "conv1"
        assert request["context"]["relay_id"] == "relay1"
        assert request["payload"]["task_config"] == {"width": 64}
        return {
            "format": "pawflow.package.runtime.result.v1",
            "ok": True,
            "flowfiles": [{
                "_content_bytes": b"out",
                "attributes": {"result": "ok"},
            }],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    result = task_cls({
        "width": 64,
        "relay": "relay1",
        "_user_id": "alice",
        "_conversation_id": "conv1",
        "_scope": "user",
    }).execute(FlowFile(content=b"in"))

    assert len(result) == 1
    assert result[0].get_content() == b"out"
    assert result[0].attributes == {"result": "ok"}


def test_pfp_runtime_host_builds_authorized_host_calls(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_tools": [{"name": "read"}],
        },
    )

    call = host.build_tool_call("read", {"path": "input.txt"})
    assert call["format"] == "pawflow.package.runtime.host_call.v1"
    assert call["kind"] == "tool"
    assert call["caller"]["package"] == "community.consumer"
    assert call["target"] == {"kind": "tool", "name": "read", "package": "", "version": ""}
    assert call["arguments"] == {"path": "input.txt"}

    try:
        host.build_tool_call("bash", {"command": "date"})
    except PackageCapabilityError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("undeclared host tool call should be denied")


def test_pfp_unqualified_grant_does_not_authorize_package_qualified_call(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, package_id="pkg.tools", include_service_provider=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["tool:reader", "service_provider:image"], force=True)

    from core.pfp_capabilities import PackageCapabilityBroker, PackageCapabilityError
    broker = PackageCapabilityBroker(user_id="alice")
    caller = {
        "package": "pkg.consumer",
        "object_id": "tool:caller",
        "allowed_tools": [{"name": "reader"}],
        "allowed_services": [{"name": "image"}],
    }

    for authorize, ref in (
            (broker.authorize_tool_call, "pkg.tools/tool:reader"),
            (broker.authorize_service_call, "pkg.tools/service:image")):
        try:
            authorize(caller, ref)
        except PackageCapabilityError as exc:
            assert "not allowed" in str(exc)
        else:
            raise AssertionError(f"unqualified grant authorized package call: {ref}")


def test_pfp_runtime_host_from_invocation_uses_envelope_context_and_grants(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core import pfp_runtime
    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    request = pfp_runtime.build_tool_invocation(
        stored["package_runtime"], stored["installed_from"], {}, {
            "user_id": "alice",
            "conversation_id": "conv1",
            "agent_name": "agentA",
            "scope": "conversation",
        })
    host = pfp_runtime.runtime_host_from_invocation(request)

    assert host.user_id == "alice"
    assert host.conversation_id == "conv1"
    assert host.scope == "conversation"
    assert host.caller_runtime["agent_name"] == "agentA"
    assert host.caller_runtime["allowed_tools"] == [{"name": "read"}]
    assert host.build_tool_call("read", {})["target"]["name"] == "read"


def test_tool_relay_pfp_host_call_rejects_forged_context(monkeypatch):
    from core import pfp_runtime
    from services.tool_relay_service import ToolRelayService

    def _must_not_build_host(*_args, **_kwargs):
        raise AssertionError("forged PFP host-call reached runtime host")

    monkeypatch.setattr(
        pfp_runtime, "runtime_host_from_invocation", _must_not_build_host)
    svc = ToolRelayService({})
    result = svc._handle_pfp_host_call(
        "rid1",
        {
            "format": pfp_runtime.RUNTIME_INVOKE_FORMAT,
            "context": {
                "user_id": "bob",
                "conversation_id": "conv1",
                "agent_name": "agentA",
            },
            "package": {"package": "community.fake", "object_id": "tool:reader"},
        },
        {"format": pfp_runtime.HOST_CALL_FORMAT, "kind": "tool", "target": "read"},
        "alice",
        "conv1",
        "agentA",
    )

    assert result["type"] == "result"
    assert result["data"]["ok"] is False
    assert "context mismatch: user_id" in result["data"]["error"]


def test_pfp_dynamic_tool_handler_passes_agent_name_to_runtime(monkeypatch):
    from core import pfp_runtime
    from core.handlers.dynamic_tool import PfpToolProxyHandler

    calls = []

    def _invoke_tool(runtime, installed_from, arguments, context):
        calls.append({
            "runtime": runtime,
            "installed_from": installed_from,
            "arguments": arguments,
            "context": context,
        })
        return "ok"

    monkeypatch.setattr(pfp_runtime, "invoke_tool", _invoke_tool)
    handler = PfpToolProxyHandler(
        "reader", "Read via package", {},
        {"package": "community.reader"},
        {"scope": "conversation"},
    )
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_agent_name("agentA")

    assert handler.execute({"path": "input.txt"}) == "ok"
    assert calls[0]["context"] == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "agent_name": "agentA",
        "scope": "conversation",
    }


def test_pfp_media_handlers_pass_agent_name_to_runtime_services(monkeypatch):
    from core.handlers.capabilities import UpscaleImageHandler
    from core.handlers.media import VideoGenerationHandler

    class _Storage:
        def write(self, destination, filename, data, content_type):
            return {"file_id": "video-file"}

    monkeypatch.setattr(
        "core.storage_resolver.StorageResolver",
        lambda user_id="", conversation_id="": _Storage(),
    )

    class _VideoService:
        def __init__(self):
            self.context = None

        def set_runtime_context(self, **kwargs):
            self.context = kwargs

        def generate(self, **kwargs):
            return {"video_bytes": b"mp4", "content_type": "video/mp4"}

    video_service = _VideoService()
    video = VideoGenerationHandler()
    video.set_user_id("alice")
    video.set_conversation_id("conv1")
    video.set_agent_name("agentA")
    video.set_service_resolver(lambda: (video_service, ""))

    assert "Video generated" in video.execute({"prompt": "spin"})
    assert video_service.context == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "agent_name": "agentA",
    }

    class _CapabilityService:
        def __init__(self):
            self.context = None

        def set_runtime_context(self, **kwargs):
            self.context = kwargs

    capability_service = _CapabilityService()
    upscale = UpscaleImageHandler()
    upscale.set_user_id("alice")
    upscale.set_conversation_id("conv1")
    upscale.set_agent_name("agentA")
    upscale.set_service_resolver(lambda: (capability_service, ""))

    service, error = upscale._get_service({})
    assert error == ""
    assert service is capability_service
    assert capability_service.context == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "agent_name": "agentA",
    }


def test_pfp_package_runtime_service_exposes_declared_media_operations(tmp_path):
    import services.package_runtime_service  # noqa: F401
    from services.package_runtime_service import PackageRuntimeService

    svc = PackageRuntimeService({
        "package_runtime": {
            "package": "community.media",
            "version": "1.0.0",
            "object_id": "service_provider:upscale",
            "provides": ["media.image_upscale"],
        },
        "installed_from": {"hash": "sha256:test"},
        "operations": {"upscale": {}},
    })

    assert hasattr(svc, "upscale")
    assert not hasattr(svc, "try_on")


def test_pfp_media_resolver_discovers_package_runtime_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_CONV
    from tasks.ai.agent_utils import AgentUtilsMixin

    reg = ServiceRegistry.get_instance()
    reg.install(SCOPE_CONV, "conv1", "pfp-image", "packageRuntime", config={
        "package_runtime": {
            "package": "community.media",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {"hash": "sha256:test"},
        "operations": {"generate": {}},
    })

    class _Agent(AgentUtilsMixin):
        pass

    svc, error = _Agent()._make_image_resolver("alice", "conv1", "agentA")()

    assert error is None
    assert svc is not None
    assert svc.TYPE == "packageRuntime"
    assert svc.config["package_runtime"]["object_id"] == "service_provider:image"


def test_tool_relay_media_resolver_discovers_pfp_capability_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from services.tool_relay_service import ToolRelayService

    ServiceRegistry.get_instance().install(
        SCOPE_USER, "alice", "pfp-3d", "packageRuntime", config={
            "package_runtime": {
                "package": "community.media",
                "version": "1.0.0",
                "object_id": "service_provider:threed",
                "provides": ["media.3d_generation"],
            },
            "installed_from": {"hash": "sha256:test"},
            "operations": {"generate_3d": {}},
        })

    svc, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "3d")()

    assert error is None
    assert svc is not None
    assert svc.TYPE == "packageRuntime"
    assert hasattr(svc, "generate_3d")


def test_tool_relay_media_resolver_accepts_native_capability_methods(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from services.base_capabilities import BaseTryOnService
    from services.tool_relay_service import ToolRelayService

    class _TryOnService(BaseTryOnService):
        TYPE = "nativeTryOnForPfpTest"

        def connect(self):
            pass

        def disconnect(self):
            pass

        def is_connected(self):
            return True

        def try_on(self, **kwargs):
            return {"image_bytes": b"PNG", "content_type": "image/png"}

    from core import ServiceFactory
    ServiceFactory.register(_TryOnService)
    ServiceRegistry.get_instance().install(
        SCOPE_USER, "alice", "tryon-native", "nativeTryOnForPfpTest")

    svc, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "tryon")()

    assert error is None
    assert svc is not None
    assert hasattr(svc, "try_on")


def test_pfp_capability_handler_persists_path_artifact(tmp_path, monkeypatch):
    from core.handlers.capabilities import UpscaleImageHandler

    source = tmp_path / "upscaled.png"
    source.write_bytes(b"PNG")
    writes = []

    class _Storage:
        def write_file(self, destination, filename, source_path, content_type):
            writes.append({
                "destination": destination,
                "filename": filename,
                "source_path": source_path,
                "content_type": content_type,
            })
            return {"file_id": "path-file"}

    monkeypatch.setattr(
        "core.storage_resolver.StorageResolver",
        lambda user_id="", conversation_id="": _Storage(),
    )

    class _Service:
        def set_runtime_context(self, **kwargs):
            pass

        def upscale(self, **kwargs):
            return {
                "image_path": str(source),
                "content_type": "image/png",
                "_delete_media_path": True,
            }

    handler = UpscaleImageHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_service_resolver(lambda: (_Service(), ""))

    result = handler.execute({"image_url": "https://example.test/input.png"})

    assert "Upscaled image" in result
    assert writes == [{
        "destination": "filestore",
        "filename": writes[0]["filename"],
        "source_path": str(source),
        "content_type": "image/png",
    }]
    assert not source.exists()


def test_pfp_speak_handler_persists_audio_path_artifact(tmp_path, monkeypatch):
    from core.file_store import FileStore
    from core import voice_clone_cache as _cache
    from core.handlers.capabilities import SpeakHandler

    _reset_repo(tmp_path, monkeypatch)
    FileStore._instance = FileStore(base_dir=str(tmp_path / "filestore"))
    source = tmp_path / "speech.mp3"
    source.write_bytes(b"AUDIO")

    _cache.save("alice", {
        "name": "pfpvoice",
        "provider": "pfp:community.voice/service_provider:voice",
        "provider_version": "1.0.0:sha256:test",
        "ref_audio_hash": "refhash",
        "ref_audio_fid": "",
        "reference_text": "",
    })

    class _PfpVoiceService:
        TYPE = "packageRuntime"
        VERSION = "1.0.0"
        config = {
            "package_runtime": {
                "package": "community.voice",
                "version": "1.0.0",
                "object_id": "service_provider:voice",
            },
            "installed_from": {"hash": "sha256:test"},
        }

        def set_runtime_context(self, **kwargs):
            pass

        def clone_speak(self, **kwargs):
            return {
                "audio_path": str(source),
                "content_type": "audio/mpeg",
                "_delete_media_path": True,
            }

    handler = SpeakHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_service_resolver(lambda: (_PfpVoiceService(), ""))

    result = handler.execute({"voice": "pfpvoice", "text": "hello"})

    assert "Speech synthesized" in result
    assert "file_id:" in result
    assert not source.exists()


def test_pfp_voice_provider_identity_includes_package_runtime():
    from core.handlers.capabilities import _provider_identity, _provider_version

    class _PfpVoiceService:
        TYPE = "packageRuntime"
        VERSION = "1.0.0"
        config = {
            "package_runtime": {
                "package": "community.voice",
                "version": "2.0.0",
                "object_id": "service_provider:voice",
            },
            "installed_from": {"hash": "sha256:abc"},
        }

    svc = _PfpVoiceService()

    assert _provider_identity(svc) == "pfp:community.voice/service_provider:voice"
    assert _provider_version(svc) == "2.0.0:sha256:abc"


def test_pfp_voice_id_result_is_normalized_for_speak_and_delete(tmp_path, monkeypatch):
    from core.file_store import FileStore
    from core import voice_clone_cache as _cache
    from core.handlers.capabilities import (
        CloneVoiceHandler, DeleteVoiceHandler, SpeakHandler,
    )

    _reset_repo(tmp_path, monkeypatch)
    FileStore._instance = FileStore(base_dir=str(tmp_path / "filestore"))

    class _Response:
        headers = {"Content-Type": "audio/mpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"REFERENCE"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response())

    class _PfpVoiceService:
        TYPE = "packageRuntime"
        VERSION = "1.0.0"
        config = {
            "package_runtime": {
                "package": "community.voice",
                "version": "1.0.0",
                "object_id": "service_provider:voice",
            },
            "installed_from": {"hash": "sha256:test"},
        }

        def __init__(self):
            self.speak_voice_id = None
            self.deleted_voice_id = None

        def ensure_voice_id(self, **kwargs):
            return {"voice_id": "vid-1"}

        def clone_speak(self, **kwargs):
            self.speak_voice_id = kwargs.get("voice_id")
            return {"audio_bytes": b"AUDIO", "content_type": "audio/mpeg"}

        def delete_voice_id(self, voice_id):
            self.deleted_voice_id = voice_id
            return True

    svc = _PfpVoiceService()

    clone = CloneVoiceHandler()
    clone.set_user_id("alice")
    clone.set_conversation_id("conv1")
    clone.set_service_resolver(lambda: (svc, ""))
    registered = clone.execute({
        "name": "pfpvoice",
        "reference_audio_url": "https://example.test/ref.mp3",
    })

    entry = _cache.get_by_name("alice", "pfpvoice")
    assert "Voice clone registered" in registered
    assert entry["voice_id"] == "vid-1"

    speak = SpeakHandler()
    speak.set_user_id("alice")
    speak.set_conversation_id("conv1")
    speak.set_service_resolver(lambda: (svc, ""))
    spoken = speak.execute({"voice": "pfpvoice", "text": "hello"})

    delete = DeleteVoiceHandler()
    delete.set_user_id("alice")
    delete.set_conversation_id("conv1")
    delete.set_service_resolver(lambda: (svc, ""))
    deleted = delete.execute({"voice": "pfpvoice"})

    assert "Speech synthesized" in spoken
    assert svc.speak_voice_id == "vid-1"
    assert "Provider voice_id freed" in deleted
    assert svc.deleted_voice_id == "vid-1"


def test_pfp_user_scoped_flow_is_visible_deployable_and_runnable(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.deployment_registry import DeploymentRegistry
    from core.handlers.flow_management import FlowManagerHandler
    from core.repository import ScopedRepository
    from tasks import register_all_tasks

    DeploymentRegistry.reset()
    register_all_tasks()
    flow = {
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "tasks": {
            "gen": {
                "type": "generateFlowFile",
                "parameters": {"content": "from pfp flow", "count": 1},
            }
        },
        "relations": [],
    }
    ScopedRepository.instance().create_flow(
        "community.pkg.demo:1.0.0", "user", flow, user_id="alice")

    handler = FlowManagerHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_agent_name("agentA")

    catalog = handler.execute({"action": "catalog"})
    deployed = handler.execute({
        "action": "deploy",
        "template_id": "community.pkg.demo:1.0.0",
    })
    run = handler.execute({
        "action": "run",
        "template_id": "community.pkg.demo:1.0.0",
    })
    instances = DeploymentRegistry.get_instance().get_by_conversation(
        "conv1", owner="alice")

    assert "community.pkg.demo:1.0.0" in catalog
    assert "deployed as instance" in deployed
    assert len(instances) == 1
    assert instances[0].flow_scope == "user"
    assert "from pfp flow" in run


def test_pfp_uninstall_removes_installed_flow(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.repository import ScopedRepository

    keypair = pfp_package.create_signing_key()
    pkgdir = tmp_path / "flow-package.pfpdir"
    flow_dir = pkgdir / "content" / "flows"
    flow_dir.mkdir(parents=True)
    (flow_dir / "demo.json").write_text(json.dumps({
        "id": "demo",
        "name": "Demo",
        "tasks": {},
        "relations": [],
    }), encoding="utf-8")
    (pkgdir / "pfp.json").write_text(json.dumps({
        "format": "pawflow.package.v1",
        "package": "community.flowpkg",
        "version": "1.0.0",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "objects": [{
            "id": "flow:demo",
            "type": "flow",
            "name": "demo",
            "fqn": "community.flowpkg.demo:1.0.0",
            "path": "content/flows/demo.json",
        }],
    }), encoding="utf-8")

    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    installed = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow:demo"], force=True)
    before = ScopedRepository.instance().get_flow(
        "community.flowpkg.demo:1.0.0", "user", user_id="alice")

    removed = pfp_package.uninstall_pfp(
        "community.flowpkg", user_id="alice", force=True)
    after = ScopedRepository.instance().get_flow(
        "community.flowpkg.demo:1.0.0", "user", user_id="alice")

    assert installed["ok"] is True
    assert before is not None
    assert removed["ok"] is True
    assert removed["removed"][0]["kind"] == "flow"
    assert after is None


def test_pfp_uninstall_keeps_modified_service_without_force(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_USER

    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path / "service-package", keypair,
        package_id="community.servicepkg",
        include_service_provider=True,
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["service_provider:image"], force=True)

    reg = ServiceRegistry.get_instance()
    sdef = reg.get_definition(SCOPE_USER, "alice", "wavespeed-image-provider")
    sdef.config["installed_from"]["hash"] = "sha256:local-change"

    result = pfp_package.uninstall_pfp(
        "community.servicepkg", user_id="alice", force=False)
    still_installed = reg.get_definition(
        SCOPE_USER, "alice", "wavespeed-image-provider")

    assert result["ok"] is False
    assert result["removed"] == []
    assert result["kept"][0]["kind"] == "service"
    assert still_installed is not None


def test_admin_start_uses_scoped_flow_fqn_without_flow_path(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.deployment_registry import DeployedInstance, DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.repository import ScopedRepository
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks.io.admin_actions import _admin_start_flow

    DeploymentRegistry.reset()
    ExecutorRegistry._instance = None
    ScopedRepository.instance().create_flow(
        "community.pkg.admin:1.0.0", "user", {
            "id": "admin-flow",
            "name": "Admin Flow",
            "tasks": {},
            "relations": [],
        }, user_id="alice")
    monkeypatch.setattr(ContinuousFlowExecutor, "start", lambda self: None)
    dep = DeploymentRegistry.get_instance()
    dep._instances["admin-inst"] = DeployedInstance(
        instance_id="admin-inst",
        flow_id="admin-flow",
        flow_name="Admin Flow",
        flow_fqn="community.pkg.admin:1.0.0",
        flow_scope="user",
        flow_path=str(tmp_path / "missing.json"),
        owner="alice",
        status="stopped",
    )

    result = _admin_start_flow(
        {"instance_id": "admin-inst"}, ExecutorRegistry.get_instance(), dep, None, None)
    executor = ExecutorRegistry.get_instance().get("admin-inst")

    assert result == {"status": "running"}
    assert executor is not None
    assert executor._flow.id == "admin-flow"


def test_files_fs_start_uses_scoped_flow_fqn_without_flow_path(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import FlowFile
    from core.deployment_registry import DeployedInstance, DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.repository import ScopedRepository
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks.ai.actions.files_fs import _handle_files_fs

    DeploymentRegistry.reset()
    ExecutorRegistry._instance = None
    ScopedRepository.instance().create_flow(
        "community.pkg.files:1.0.0", "user", {
            "id": "files-flow",
            "name": "Files Flow",
            "tasks": {},
            "relations": [],
        }, user_id="alice")
    monkeypatch.setattr(ContinuousFlowExecutor, "start", lambda self: None)
    dep = DeploymentRegistry.get_instance()
    dep._instances["files-inst"] = DeployedInstance(
        instance_id="files-inst",
        flow_id="files-flow",
        flow_name="Files Flow",
        flow_fqn="community.pkg.files:1.0.0",
        flow_scope="user",
        flow_path=str(tmp_path / "missing.json"),
        owner="alice",
        status="stopped",
    )
    flowfile = FlowFile()

    _handle_files_fs(None, "manage_conv_flow", {
        "flow_id": "files-inst",
        "flow_action": "start",
    }, None, "alice", flowfile)
    result = json.loads(flowfile.get_content().decode("utf-8"))
    executor = ExecutorRegistry.get_instance().get("files-inst")

    assert result == {"message": "Flow 'files-inst' started"}
    assert executor is not None
    assert executor._flow.id == "files-flow"


def test_service_flow_deploy_and_start_preserve_pfp_runtime_identity(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import FlowFile
    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.repository import ScopedRepository
    from tasks.ai.actions.service_flow import _handle_service_flow

    DeploymentRegistry.reset()
    ExecutorRegistry._instance = None
    ScopedRepository.instance().create_flow(
        "community.pkg.service:1.0.0", "user", {
            "id": "service-flow",
            "name": "Service Flow",
            "scope": "conversation",
            "tasks": {},
            "relations": [],
        }, user_id="alice")

    class _Agent:
        _agent_name = "agentA"

    deploy_ff = FlowFile()
    _handle_service_flow(_Agent(), "deploy_flow", {
        "template_id": "community.pkg.service:1.0.0",
        "conversation_id": "conv1",
    }, None, "alice", deploy_ff)
    deployed = json.loads(deploy_ff.get_content().decode("utf-8"))
    inst = DeploymentRegistry.get_instance().get(deployed["instance_id"])

    captured = {}

    def _capture_restore(self, instance_id, flow_path, max_workers=4,
                         max_retries=3, **kwargs):
        captured.update({
            "instance_id": instance_id,
            "flow_path": flow_path,
            "max_workers": max_workers,
            "max_retries": max_retries,
            **kwargs,
        })
        return True

    monkeypatch.setattr(ExecutorRegistry, "_restore_instance", _capture_restore)
    start_ff = FlowFile()
    _handle_service_flow(_Agent(), "start_flow", {
        "instance_id": deployed["instance_id"],
    }, None, "alice", start_ff)
    started = json.loads(start_ff.get_content().decode("utf-8"))

    assert deployed["ok"] is True
    assert inst.flow_fqn == "community.pkg.service:1.0.0"
    assert inst.flow_scope == "conversation"
    assert inst.agent_name == "agentA"
    assert started == {"ok": True, "status": "running"}
    assert captured["flow_fqn"] == "community.pkg.service:1.0.0"
    assert captured["flow_scope"] == "conversation"
    assert captured["owner"] == "alice"
    assert captured["conversation_id"] == "conv1"
    assert captured["agent_name"] == "agentA"


def test_service_flow_discovers_and_deploys_conversation_scoped_pfp_flow(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import FlowFile
    from core.deployment_registry import DeploymentRegistry
    from core.repository import ScopedRepository
    from tasks.ai.actions.service_flow import _handle_service_flow

    DeploymentRegistry.reset()
    ScopedRepository.instance().create_flow(
        "community.pkg.convflow:1.0.0", "conv", {
            "id": "conv-flow",
            "name": "Conversation Flow",
            "scope": "conversation",
            "tasks": {},
            "relations": [],
        }, user_id="alice", conv_id="conv1")

    class _Agent:
        _agent_name = "agentA"

    list_ff = FlowFile()
    _handle_service_flow(_Agent(), "list_available_flows", {
        "conversation_id": "conv1",
    }, None, "alice", list_ff)
    listed = json.loads(list_ff.get_content().decode("utf-8"))

    schema_ff = FlowFile()
    _handle_service_flow(_Agent(), "get_flow_deploy_schema", {
        "template_id": "community.pkg.convflow:1.0.0",
        "conversation_id": "conv1",
    }, None, "alice", schema_ff)
    schema = json.loads(schema_ff.get_content().decode("utf-8"))

    deploy_ff = FlowFile()
    _handle_service_flow(_Agent(), "deploy_flow", {
        "template_id": "community.pkg.convflow:1.0.0",
        "conversation_id": "conv1",
    }, None, "alice", deploy_ff)
    deployed = json.loads(deploy_ff.get_content().decode("utf-8"))
    inst = DeploymentRegistry.get_instance().get(deployed["instance_id"])

    assert any(item["id"] == "conv-flow" and item["scope"] == "conversation"
               for item in listed["templates"])
    assert schema["template_id"] == "conv-flow"
    assert deployed["ok"] is True
    assert inst.flow_fqn == "community.pkg.convflow:1.0.0"
    assert inst.flow_scope == "conversation"
    assert inst.conversation_id == "conv1"
    assert inst.agent_name == "agentA"


def test_media_list_action_includes_conversation_scoped_pfp_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core import FlowFile
    from core.service_registry import ServiceRegistry, SCOPE_CONV
    from tasks.ai.agent_utils import AgentUtilsMixin
    from tasks.ai.actions.media import _handle_media

    ServiceRegistry.get_instance().install(
        SCOPE_CONV, "conv1", "pfp-image", "packageRuntime", config={
            "package_runtime": {
                "package": "community.media",
                "version": "1.0.0",
                "object_id": "service_provider:image",
                "provides": ["media.image_generation"],
            },
            "installed_from": {"hash": "sha256:test"},
            "operations": {"generate": {}},
        })

    class _Agent(AgentUtilsMixin):
        pass

    class _Store:
        def get_extra(self, conversation_id, key):
            return {}

    flowfile = FlowFile()
    _handle_media(
        _Agent(), "list_image_services", {"conversation_id": "conv1"},
        _Store(), "alice", flowfile)
    data = json.loads(flowfile.get_content().decode("utf-8"))

    assert any(item["id"] == "pfp-image" for item in data)


def test_executor_restore_loads_user_scoped_flow_fqn(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.executor_registry import ExecutorRegistry
    from core.repository import ScopedRepository
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks import register_all_tasks

    register_all_tasks()
    ScopedRepository.instance().create_flow(
        "community.pkg.restore:1.0.0", "user", {
            "id": "restore",
            "name": "Restore From User Scope",
            "version": "1.0.0",
            "tasks": {"gen": {"type": "generateFlowFile", "parameters": {}}},
            "relations": [],
        }, user_id="alice")
    monkeypatch.setattr(ContinuousFlowExecutor, "start", lambda self: None)

    registry = ExecutorRegistry()
    ok = registry._restore_instance(
        "inst1", "/missing/template.json",
        flow_fqn="community.pkg.restore:1.0.0",
        owner="alice",
        conversation_id="conv1",
        agent_name="agentA",
    )

    executor = registry.get("inst1")
    assert ok is True
    assert executor is not None
    assert executor._flow.id == "restore"
    assert executor._flow.source_dir.endswith(
        "repository/flows/users/alice/community/pkg/restore/versions")
    assert executor._runtime_context["user_id"] == "alice"


def test_executor_restore_preserves_flow_path_source_dir(tmp_path, monkeypatch):
    from core.executor_registry import ExecutorRegistry
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks import register_all_tasks

    register_all_tasks()
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    (asset_dir / "install.html").write_text("<h1>Install</h1>", encoding="utf-8")
    flow_path = tmp_path / "1.0.0.json"
    flow_path.write_text(json.dumps({
        "id": "installer",
        "name": "Installer",
        "version": "1.0.0",
        "tasks": {
            "ui": {
                "type": "generateFlowFile",
                "parameters": {"content_file": "install.html"},
            },
        },
        "relations": [],
    }), encoding="utf-8")
    monkeypatch.setattr(ContinuousFlowExecutor, "start", lambda self: None)

    registry = ExecutorRegistry()
    ok = registry._restore_instance("inst-file", str(flow_path))

    executor = registry.get("inst-file")
    assert ok is True
    assert executor is not None
    assert executor._flow.source_dir == str(tmp_path.resolve())
    assert executor._flow.tasks["ui"]._flow_source_dir == str(tmp_path.resolve())


def test_execute_flow_propagates_runtime_context_to_subflow(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import FlowFile
    from engine import FlowParser
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks.control.execute_flow import ExecuteFlowTask

    child_path = tmp_path / "child.json"
    child_path.write_text('{"tasks": {}, "relations": []}', encoding="utf-8")
    task = ExecuteFlowTask({"flow_path": str(child_path)})
    task.set_runtime_context(
        user_id="alice", conversation_id="conv1",
        scope="conversation", agent_name="agentA")
    captured = {}

    def _fake_run_batch(flow, input_flowfiles=None, parameters=None,
                        runtime_context=None, **_kwargs):
        captured["runtime_context"] = runtime_context
        return SimpleNamespace(success=True, errors=[], output_flowfiles=[])

    monkeypatch.setattr(FlowParser, "parse_from_file", lambda _path: SimpleNamespace(
        name="Child", parameters={}, tasks={}, relations=[]))
    monkeypatch.setattr(ContinuousFlowExecutor, "run_batch", _fake_run_batch)

    task.execute(FlowFile(content=b"x"))

    assert captured["runtime_context"] == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "agentA",
    }


def test_executor_injects_runtime_context_into_execute_flow_task(tmp_path):
    from core import Flow
    from engine.continuous_executor import ContinuousFlowExecutor
    from tasks.control.execute_flow import ExecuteFlowTask

    child_path = tmp_path / "child.json"
    child_path.write_text('{"tasks": {}, "relations": []}', encoding="utf-8")
    task = ExecuteFlowTask({"flow_path": str(child_path)})
    flow = Flow({"id": "parent", "name": "Parent", "relations": []})
    flow.add_task("exec", task)

    ContinuousFlowExecutor(flow, runtime_context={
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "agentA",
    })

    assert task._runtime_context == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "agentA",
    }


def test_pfp_task_output_flowfile_from_content_path_stays_disk_backed(tmp_path):
    from core import pfp_runtime

    content_path = tmp_path / "result.bin"
    content_path.write_bytes(b"x" * 16)

    flowfile = pfp_runtime._flowfile_from_payload({
        "content_path": str(content_path),
        "content_root": str(tmp_path),
        "_delete_content_path": True,
        "attributes": {"mime.type": "application/octet-stream"},
    })

    assert flowfile.is_content_on_disk is True
    assert flowfile.size() == 16
    assert flowfile.get_attribute("mime.type") == "application/octet-stream"


def test_pfp_flow_task_uninstall_keeps_proxy_used_by_other_user(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import TaskFactory

    keypair1 = pfp_package.create_signing_key()
    pkgdir1 = _write_package_dir(
        tmp_path / "pkg1", keypair1,
        package_id="community.one", include_flow_task=True)
    (pkgdir1 / "content" / "flow-tasks" / "image-resize" / "task.py").write_text(
        "from pawflow import pfp\npfp.result(flowfiles=[])\n",
        encoding="utf-8")
    built1 = pfp_package.build_pfp(
        str(pkgdir1), private_key=keypair1["private_key"])

    keypair2 = pfp_package.create_signing_key()
    pkgdir2 = _write_package_dir(
        tmp_path / "pkg2", keypair2,
        package_id="community.two", include_flow_task=True)
    (pkgdir2 / "content" / "flow-tasks" / "image-resize" / "task.py").write_text(
        "from pawflow import pfp\npfp.result(flowfiles=[])\n",
        encoding="utf-8")
    built2 = pfp_package.build_pfp(
        str(pkgdir2), private_key=keypair2["private_key"])

    pfp_package.install_pfp(
        built1["path"], user_id="alice",
        include=["flow_task:resize-image"], force=True)
    pfp_package.install_pfp(
        built2["path"], user_id="bob",
        include=["flow_task:resize-image"], force=True)

    assert "packageResizeImage" in TaskFactory.list_types()
    result = pfp_package.uninstall_pfp("community.one", user_id="alice", force=False)

    assert result["ok"] is True
    assert "packageResizeImage" in TaskFactory.list_types()
    proxy = TaskFactory.get("packageResizeImage")
    assert proxy.PACKAGE_RUNTIME["package"] == "community.two"


def test_pfp_flow_task_update_ignores_other_user_global_proxy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import TaskFactory

    alice_key = pfp_package.create_signing_key()
    alice_v1_dir = _write_package_dir(
        tmp_path / "alice-v1", alice_key,
        package_id="community.alice", include_flow_task=True)
    alice_v1 = pfp_package.build_pfp(
        str(alice_v1_dir), private_key=alice_key["private_key"])

    bob_key = pfp_package.create_signing_key()
    bob_dir = _write_package_dir(
        tmp_path / "bob", bob_key,
        package_id="community.bob", include_flow_task=True)
    bob = pfp_package.build_pfp(str(bob_dir), private_key=bob_key["private_key"])

    pfp_package.install_pfp(
        alice_v1["path"], user_id="alice",
        include=["flow_task:resize-image"], force=True)
    pfp_package.install_pfp(
        bob["path"], user_id="bob",
        include=["flow_task:resize-image"], force=True)
    assert TaskFactory.get("packageResizeImage").PACKAGE_RUNTIME["package"] == "community.bob"

    alice_v2_dir = _write_package_dir(
        tmp_path / "alice-v2", alice_key, version="1.1.0",
        package_id="community.alice", include_flow_task=True)
    (alice_v2_dir / "content" / "flow-tasks" / "image-resize" / "task.py").write_text(
        "from pawflow import pfp\npfp.result(flowfiles=[])\n",
        encoding="utf-8")
    alice_v2 = pfp_package.build_pfp(
        str(alice_v2_dir), private_key=alice_key["private_key"])

    updated = pfp_package.update_pfp(alice_v2["path"], user_id="alice")

    assert updated["ok"] is True
    assert {"id": "flow_task:resize-image", "reason": "local_modified"} not in updated["skipped"]
    assert updated["updated"][0]["id"] == "flow_task:resize-image"


def test_tool_relay_speech_to_video_resolves_lipsync_pfp_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from services.base_video_generation import BaseVideoGenerationService
    from services.tool_relay_service import ToolRelayService

    class FakeVideoWithoutSpeechToVideo(BaseVideoGenerationService):
        TYPE = "fakeVideoWithoutSpeechToVideo"

        def _create_connection(self):
            return self

        def _close_connection(self):
            pass

        def generate(self, **kwargs):
            return {"video_url": "native.mp4"}

    monkeypatch.setitem(
        ServiceFactory._services,
        "fakeVideoWithoutSpeechToVideo",
        FakeVideoWithoutSpeechToVideo)
    registry = ServiceRegistry.get_instance()
    registry.install(
        SCOPE_USER, "alice", "native-video", "fakeVideoWithoutSpeechToVideo",
        config={})
    registry.install(
        SCOPE_USER, "alice", "pfp-s2v", "packageRuntime", config={
            "package_runtime": {
                "package": "community.s2v",
                "version": "1.0.0",
                "object_id": "service_provider:s2v",
                "entrypoint": "content/service-providers/s2v/provider.py",
                "content_dir": str(tmp_path),
                "provides": ["media.lipsync"],
            },
            "installed_from": {"hash": "sha256:test"},
            "operations": {"speech_to_video": {}},
        })

    service, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "speech_to_video")()

    assert error is None
    assert service is not None
    assert hasattr(service, "speech_to_video")


def test_pfp_sdk_flowfile_writes_relay_local_content_path(tmp_path, monkeypatch):
    import importlib.util

    monkeypatch.chdir(tmp_path)
    sdk_path = Path(__file__).resolve().parents[1] / "docker" / "pawflow_sdk" / "pawflow.py"
    spec = importlib.util.spec_from_file_location("pawflow_sdk_under_test", sdk_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    descriptor = module.pfp.flowfile("hello", {"mime.type": "text/plain"})

    assert "content_b64" not in descriptor
    assert descriptor["content_path"].startswith(".pawflow/flowfiles/results/result-")
    assert (tmp_path / descriptor["content_path"]).read_bytes() == b"hello"
    assert descriptor["attributes"] == {"mime.type": "text/plain"}


def test_pfp_relay_runner_reports_crash_after_success_result(tmp_path):
    from core import pfp_runtime

    runner = tmp_path / "pfp_relay_runner.py"
    runner.write_text(pfp_runtime._RELAY_RUNNER, encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "format": pfp_runtime.RUNTIME_INVOKE_FORMAT,
        "kind": "tool",
        "package": {},
        "context": {},
        "payload": {},
    }), encoding="utf-8")
    entrypoint = tmp_path / "entry.py"
    entrypoint.write_text(
        "from pawflow import pfp\n"
        "pfp.result({'ok': True})\n"
        "raise RuntimeError('boom after result')\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["PAWFLOW_PFP_SDK_PATH"] = str(Path(__file__).resolve().parents[1] / "docker" / "pawflow_sdk")

    proc = subprocess.run(
        [sys.executable, str(runner), str(request), str(entrypoint)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0
    envelope = json.loads(proc.stdout)
    assert envelope["ok"] is False
    assert "exited with code 1" in envelope["error"]
    assert "boom after result" in envelope["error"]



def test_pfp_runtime_host_executes_authorized_tool_call(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError

    class _Tool:
        def __init__(self):
            self.calls = []

        def execute(self, arguments):
            self.calls.append(arguments)
            return "read-result"

    class _Registry:
        def __init__(self):
            self.tool = _Tool()

        def get(self, name):
            return self.tool if name == "read" else None

    registry = _Registry()
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_tools": [{"name": "read"}],
        },
        tool_registry=registry,
    )

    result = host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "tool",
        "target": "read",
        "arguments": {"path": "input.txt"},
    })
    assert result == "read-result"
    assert registry.tool.calls == [{"path": "input.txt"}]

    structured = host.build_tool_call("read", {"path": "second.txt"})
    assert host.handle_host_call(structured) == "read-result"
    assert registry.tool.calls[-1] == {"path": "second.txt"}

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "tool",
            "target": "bash",
            "arguments": {"command": "date"},
        })
    except PackageCapabilityError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("host-call envelope should not bypass grants")


def test_pfp_runtime_host_package_tool_ref_requires_matching_runtime(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core import pfp_capabilities

    monkeypatch.setattr(
        pfp_capabilities.PackageCapabilityBroker,
        "_require_installed_package",
        lambda self, package_id, version="", object_id="": None,
    )

    class _Tool:
        def __init__(self, runtime=None):
            self._package_runtime = runtime or {}

        def execute(self, arguments):
            return "tool-result"

    class _Registry:
        def __init__(self, tool):
            self.tool = tool

        def get(self, name):
            return self.tool if name == "normalize" else None

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_tools": [{
                "package": "community.base",
                "object": "tool:normalize",
            }],
        },
        tool_registry=_Registry(_Tool()),
    )

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "tool",
            "target": "community.base/tool:normalize",
            "arguments": {"text": "hi"},
        })
    except pfp_runtime.PackageRuntimeError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("package-qualified host call should not execute a homonymous tool")

    host.tool_registry = _Registry(_Tool({
        "package": "community.base",
        "version": "1.0.0",
        "object_id": "tool:normalize",
    }))
    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "tool",
        "target": "community.base/tool:normalize",
        "arguments": {"text": "hi"},
    }) == "tool-result"

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "tool",
            "target": "community.base@2.0.0/tool:normalize",
            "arguments": {"text": "hi"},
        })
    except pfp_runtime.PackageRuntimeError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("package-qualified host call should reject a version mismatch")


def test_pfp_runtime_host_package_tool_ref_resolves_by_object_id(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core import pfp_capabilities

    monkeypatch.setattr(
        pfp_capabilities.PackageCapabilityBroker,
        "_require_installed_package",
        lambda self, package_id, version="", object_id="": None,
    )

    class _Tool:
        name = "friendly_normalize"
        _package_runtime = {
            "package": "community.base",
            "object_id": "tool:normalize",
        }

        def execute(self, arguments):
            return f"ok:{arguments['text']}"

    class _Registry:
        def get(self, name):
            return None

        def list_tools(self):
            return [_Tool()]

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_tools": [{
                "package": "community.base",
                "object": "tool:normalize",
            }],
        },
        tool_registry=_Registry(),
    )

    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "tool",
        "target": "community.base/tool:normalize",
        "arguments": {"text": "hi"},
    }) == "ok:hi"


def test_pfp_runtime_host_executes_authorized_service_call(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError

    class _Service:
        def __init__(self):
            self.calls = []
            self.runtime_contexts = []

        def set_runtime_context(self, **kwargs):
            self.runtime_contexts.append(kwargs)

        def invoke(self, operation, arguments):
            self.calls.append((operation, arguments))
            return {"ok": True, "image": "out.png"}

    class _Registry:
        def __init__(self):
            self.service = _Service()

        def resolve(self, service_id, *, user_id="", conv_id=""):
            assert user_id == "alice"
            assert conv_id == "conv1"
            return self.service if service_id == "image-service" else None

    registry = _Registry()
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "service_provider:consumer",
            "agent_name": "agentA",
            "allowed_services": [{"name": "image-service"}],
        },
        service_registry=registry,
    )

    result = host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "service",
        "target": "image-service",
        "operation": "generate",
        "arguments": {"prompt": "cat"},
    })
    assert result == {"ok": True, "image": "out.png"}
    assert registry.service.calls == [("generate", {"prompt": "cat"})]
    assert registry.service.runtime_contexts == [{
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "agentA",
    }]

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "service",
            "target": "secret-service",
            "operation": "read",
            "arguments": {},
        })
    except PackageCapabilityError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("undeclared host service call should be denied")


def test_pfp_runtime_host_dispatches_builtin_service_operations(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime

    class _BuiltinService:
        def __init__(self):
            self.values = {}
            self.runtime_contexts = []

        def set_runtime_context(self, **kwargs):
            self.runtime_contexts.append(kwargs)

        def put(self, key, value):
            self.values[key] = value

        def get(self, key, default=None):
            return self.values.get(key, default)

        def get_blob(self):
            return b"abc"

        def disconnect(self):
            raise AssertionError("lifecycle methods must not be dispatched")

        def ensure_connected(self):
            raise AssertionError("lifecycle methods must not be dispatched")

        def reset(self):
            raise AssertionError("destructive reset must not be dispatched")

    class _Registry:
        def __init__(self):
            self.service = _BuiltinService()

        def resolve(self, service_id, *, user_id="", conv_id=""):
            assert user_id == "alice"
            assert conv_id == "conv1"
            return self.service if service_id == "cache1" else None

    registry = _Registry()
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "agent_name": "agentA",
            "allowed_services": [{"name": "cache1"}],
        },
        service_registry=registry,
    )

    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "service",
        "target": "cache1",
        "operation": "put",
        "arguments": {"key": "answer", "value": 42},
    }) is None
    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "service",
        "target": "cache1",
        "operation": "get",
        "arguments": {"key": "answer"},
    }) == 42
    assert registry.service.runtime_contexts[0] == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
        "agent_name": "agentA",
    }

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "service",
            "target": "cache1",
            "operation": "disconnect",
            "arguments": {},
        })
    except pfp_runtime.PackageRuntimeError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("PFP host service calls must not dispatch lifecycle methods")

    for operation in ("ensure_connected", "reset"):
        try:
            host.handle_host_call({
                "format": "pawflow.package.runtime.host_call.v1",
                "kind": "service",
                "target": "cache1",
                "operation": operation,
                "arguments": {},
            })
        except pfp_runtime.PackageRuntimeError as exc:
            assert "not available" in str(exc)
        else:
            raise AssertionError(f"PFP host service calls must not dispatch {operation}")

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "service",
            "target": "cache1",
            "operation": "get_blob",
            "arguments": {},
        })
    except pfp_runtime.PackageRuntimeError as exc:
        assert "non-JSON" in str(exc)
    else:
        raise AssertionError("PFP host service calls must reject non-JSON results")


def test_pfp_runtime_host_package_service_ref_requires_matching_runtime(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core import pfp_capabilities

    monkeypatch.setattr(
        pfp_capabilities.PackageCapabilityBroker,
        "_require_installed_package",
        lambda self, package_id, version="", object_id="": None,
    )

    class _Service:
        def __init__(self, runtime=None):
            self.config = {"package_runtime": runtime or {}}

        def invoke(self, operation, arguments):
            return {"ok": True, "operation": operation}

    class _Registry:
        def __init__(self, service):
            self.service = service
            self.definition = type("_Def", (), {
                "service_id": "image-service",
                "scope": "user",
                "scope_id": "alice",
                "config": service.config,
            })()

        def resolve(self, service_id, *, user_id="", conv_id=""):
            return self.service if service_id == "image-service" else None

        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            return [self.definition] if service_type == "packageRuntime" else []

        def get_live_instance(self, scope, scope_id, service_id):
            return self.service if service_id == "image-service" else None

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_services": [{
                "package": "community.media",
                "object": "service:image",
            }],
        },
        service_registry=_Registry(_Service()),
    )

    try:
        host.handle_host_call({
            "format": "pawflow.package.runtime.host_call.v1",
            "kind": "service",
            "target": "community.media/service:image",
            "operation": "generate",
            "arguments": {"prompt": "cat"},
        })
    except pfp_runtime.PackageRuntimeError as exc:
        assert "not available" in str(exc)
    else:
        raise AssertionError("package-qualified host call should not execute a homonymous service")

    host.service_registry = _Registry(_Service({
        "package": "community.media",
        "object_id": "service_provider:image",
    }))
    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "service",
        "target": "community.media/service:image",
        "operation": "generate",
        "arguments": {"prompt": "cat"},
    }) == {"ok": True, "operation": "generate"}


def test_package_runtime_service_requires_declared_operations(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.base_service import ServiceError
    from services.package_runtime_service import PackageRuntimeService

    service = PackageRuntimeService({
        "package_runtime": {
            "package": "community.open",
            "version": "1.0.0",
            "object_id": "service_provider:any",
            "entrypoint": "provider.py",
            "content_dir": str(tmp_path),
            "runner": "python",
        },
        "installed_from": {"hash": "sha256:test"},
        "operations": {},
    })
    service.connect()

    def _unexpected_invoke(*args, **kwargs):
        raise AssertionError("undeclared PFP service operation must not reach runtime")

    monkeypatch.setattr(pfp_runtime, "invoke_service", _unexpected_invoke)
    try:
        service.invoke("delete_everything", {"x": 1})
    except ServiceError as exc:
        assert "declares no operations" in str(exc)
    else:
        raise AssertionError("PFP service provider without operations must reject invocation")


def test_pfp_runtime_host_package_service_ref_resolves_installed_service_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, package_id="community.media",
        include_service_provider=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["service_provider:image"], force=True)

    from core import pfp_runtime
    from core.service_registry import ServiceRegistry

    calls = []

    def _invoke_service(runtime, installed_from, operation, arguments, context):
        calls.append({
            "runtime": runtime,
            "operation": operation,
            "arguments": arguments,
            "context": context,
        })
        return {"ok": True, "service_id": runtime["object_id"]}

    monkeypatch.setattr(pfp_runtime, "invoke_service", _invoke_service)
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        conversation_id="conv1",
        scope="conversation",
        caller_runtime={
            "package": "community.consumer",
            "version": "1.0.0",
            "object_id": "tool:consumer",
            "allowed_services": [{
                "package": "community.media",
                "object": "service:image",
            }],
        },
        service_registry=ServiceRegistry.get_instance(),
    )

    assert host.handle_host_call({
        "format": "pawflow.package.runtime.host_call.v1",
        "kind": "service",
        "target": "community.media/service:image",
        "operation": "generate",
        "arguments": {"prompt": "cat"},
    }) == {"ok": True, "service_id": "service_provider:image"}
    assert calls[0]["runtime"]["object_id"] == "service_provider:image"
    assert calls[0]["operation"] == "generate"


def test_pfp_installs_service_provider_as_package_runtime_proxy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_service_provider=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    provider_row = next(row for row in plan["objects"] if row["id"] == "service_provider:image")
    assert provider_row["status"] == "new"
    assert provider_row["risk"] == "high"

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["service_provider:image"], force=True)
    assert result["ok"] is True

    from core.service_registry import ServiceRegistry, SCOPE_USER
    sdef = ServiceRegistry.get_instance().get_definition(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    assert sdef is not None
    assert sdef.service_type == "packageRuntime"
    assert sdef.config["package_runtime"]["object_id"] == "service_provider:image"
    assert sdef.config["package_runtime"]["allowed_tools"] == [{"name": "read"}]
    assert sdef.config["package_runtime_context"] == {
        "user_id": "alice",
        "conversation_id": "",
        "scope": "user",
    }
    content_dir = Path(sdef.config["package_runtime"]["content_dir"])
    assert (content_dir / sdef.config["package_runtime"]["entrypoint"]).exists()

    live = ServiceRegistry.get_instance().get_live_instance(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    assert live is not None
    assert live.is_connected() is True
    from core import ServiceError
    try:
        live.invoke("generate", {})
    except ServiceError as exc:
        assert "PFP runtime requires user_id and conversation_id" in str(exc)
    else:
        raise AssertionError("package runtime proxy should fail closed")


def test_pfp_service_provider_executes_declared_python_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True,
        service_runner="python")
    entrypoint = pkgdir / "content" / "service-providers" / "image" / "provider.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "payload = request['payload']\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': {\n"
        "        'operation': payload['operation'],\n"
        "        'prompt': payload['arguments']['prompt'],\n"
        "    },\n"
        "}))\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["service_provider:image"], force=True)

    from core.service_registry import ServiceRegistry, SCOPE_USER

    live = ServiceRegistry.get_instance().get_live_instance(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    live.set_runtime_context(
        user_id="alice", conversation_id="conv1", agent_name="agentA")
    from core import pfp_runtime

    def _invoke(self, request):
        assert request["context"]["user_id"] == "alice"
        assert request["context"]["conversation_id"] == "conv1"
        assert request["context"]["agent_name"] == "agentA"
        payload = request["payload"]
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "result": {
                "operation": payload["operation"],
                "prompt": payload["arguments"]["prompt"],
            },
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    assert live.invoke("generate", {"prompt": "cat"}) == {
        "operation": "generate",
        "prompt": "cat",
    }


def test_pfp_service_provider_exposes_lifecycle_status_and_operations(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True,
        service_runner="python")
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = next(obj for obj in manifest["objects"] if obj["id"] == "service_provider:image")
    provider["operations"] = {
        "generate": {"description": "Generate an image"},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entrypoint = pkgdir / "content" / "service-providers" / "image" / "provider.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': {'ok': True, 'operation': request['payload']['operation']},\n"
        "}))\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["service_provider:image"], force=True)

    from core import ServiceError
    from core.service_registry import ServiceRegistry, SCOPE_USER
    live = ServiceRegistry.get_instance().get_live_instance(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    from core import pfp_runtime

    def _invoke(self, request):
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "result": {"ok": True, "operation": request["payload"]["operation"]},
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    assert live.is_connected() is True
    assert live.get_operations() == {"generate": {"description": "Generate an image"}}
    assert live.status()["package"] == "community.wavespeed"
    assert live.get_model_info()["operations"] == live.get_operations()
    assert live.invoke("generate", {}) == {"ok": True, "operation": "generate"}
    try:
        live.invoke("unknown", {})
    except ServiceError as exc:
        assert "not declared" in str(exc)
    else:
        raise AssertionError("undeclared service operation should fail")
    live.disconnect()
    assert live.is_connected() is False


def test_pfp_dev_load_service_provider_uses_source_dir_and_file_artifacts(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True,
        service_runner="python")
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = next(obj for obj in manifest["objects"] if obj["id"] == "service_provider:image")
    provider["operations"] = {"generate": {"description": "Generate an image"}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entrypoint = pkgdir / "content" / "service-providers" / "image" / "provider.py"
    entrypoint.write_text(
        "from pathlib import Path\n"
        "from pawflow import pfp\n"
        "out = Path(pfp.context['output_dir']) / 'image.png'\n"
        "out.write_bytes(b'PNG1')\n"
        "pfp.result(pfp.artifact('image', 'image.png', 'image/png'))\n",
        encoding="utf-8",
    )

    loaded = pfp_package.dev_load_pfp(
        str(pkgdir), user_id="alice", conversation_id="conv1",
        include=["service_provider:image"])
    assert loaded["ok"] is True
    assert loaded["dev"] is True

    from core.service_registry import ServiceRegistry, SCOPE_CONV
    live = ServiceRegistry.get_instance().get_live_instance(
        SCOPE_CONV, "conv1", "wavespeed-image-provider")
    live.set_runtime_context(user_id="alice", conversation_id="conv1")
    from core import pfp_runtime

    def _invoke(self, request):
        output_dir = Path(request["context"]["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        provider_source = entrypoint.read_text(encoding="utf-8")
        payload = b"PNG2" if "PNG2" in provider_source else b"PNG1"
        (output_dir / "image.png").write_bytes(payload)
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "result": {"artifact": {
                "kind": "image",
                "path": "image.png",
                "content_type": "image/png",
            }},
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    first = live.generate(prompt="cat")
    try:
        assert Path(first["image_path"]).read_bytes() == b"PNG1"
        assert first["content_type"] == "image/png"
        assert first["artifact"]["size"] == 4
        assert first["artifact"]["sha256"].startswith("sha256:")
        assert "image_bytes" not in first
    finally:
        Path(first["image_path"]).unlink(missing_ok=True)

    entrypoint.write_text(
        "from pathlib import Path\n"
        "from pawflow import pfp\n"
        "out = Path(pfp.context['output_dir']) / 'image.png'\n"
        "out.write_bytes(b'PNG2')\n"
        "pfp.result(pfp.artifact('image', 'image.png', 'image/png'))\n",
        encoding="utf-8",
    )
    second = live.generate(prompt="cat")
    try:
        assert Path(second["image_path"]).read_bytes() == b"PNG2"
    finally:
        Path(second["image_path"]).unlink(missing_ok=True)

    unloaded = pfp_package.dev_unload_pfp(
        "community.wavespeed", user_id="alice", conversation_id="conv1")
    assert unloaded["ok"] is True
    assert ServiceRegistry.get_instance().get_definition(
        SCOPE_CONV, "conv1", "wavespeed-image-provider") is None


def test_pfp_installs_flow_task_as_taskfactory_proxy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")
    task_row = next(row for row in plan["objects"] if row["id"] == "flow_task:resize-image")
    assert task_row["status"] == "new"
    assert task_row["risk"] == "high"

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)
    assert result["ok"] is True

    from core import TaskFactory
    task_cls = TaskFactory.get("packageResizeImage")
    assert task_cls.TYPE == "packageResizeImage"
    assert task_cls.PACKAGE_RUNTIME["object_id"] == "flow_task:resize-image"
    assert task_cls.INSTALLED_FROM["package"] == "community.wavespeed"
    content_dir = Path(task_cls.PACKAGE_RUNTIME["content_dir"])
    assert (content_dir / task_cls.PACKAGE_RUNTIME["entrypoint"]).exists()
    assert task_cls({"width": 64, "relay": "relay1"}).get_parameter_schema()["width"]["type"] == "integer"

    from engine.validator import FlowValidator
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64, "relay": "relay1"}}},
        "relations": [],
    })
    assert validation.valid is True
    assert not any("not registered" in warning for warning in validation.warnings)

    TaskFactory._tasks.pop("packageResizeImage", None)
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64, "relay": "relay1"}}},
        "relations": [],
    })
    assert any("not registered" in warning for warning in validation.warnings)
    reloaded = pfp_package.load_installed_package_tasks(user_id="alice")
    assert reloaded["loaded"][0]["task_type"] == "packageResizeImage"
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64, "relay": "relay1"}}},
        "relations": [],
    })
    assert not any("not registered" in warning for warning in validation.warnings)
    task_cls = TaskFactory.get("packageResizeImage")

    try:
        task_cls({"width": 64})
    except ValueError as exc:
        assert "relay" in str(exc)
    else:
        raise AssertionError("package flow task proxy should require relay")

    removed = pfp_package.uninstall_pfp("community.wavespeed", user_id="alice", force=True)
    assert removed["ok"] is True
    assert "packageResizeImage" not in TaskFactory.list_types()
    assert not content_dir.exists()


def test_pfp_flow_task_executes_declared_python_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "from pathlib import Path\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = Path(payload['flowfile']['content_path']).read_text()\n"
        "width = payload['task_config']['width']\n"
        "pfp.result(flowfiles=[pfp.flowfile(f'{content}:{width}', {'resized': width})])\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime

    def _invoke(self, request):
        assert request["context"]["user_id"] == "alice"
        assert request["context"]["conversation_id"] == "conv1"
        payload = request["payload"]
        content = payload["flowfile"]["_content_bytes"].decode("utf-8")
        width = payload["task_config"]["width"]
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=f"{content}:{width}".encode("utf-8"), attributes={"resized": str(width)}))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    task_cls = TaskFactory.get("packageResizeImage")
    result = task_cls({
        "width": 64,
        "relay": "relay1",
        "_user_id": "alice",
        "_conversation_id": "conv1",
        "_scope": "user",
    }).execute(FlowFile(content=b"img"))

    assert len(result) == 1
    assert result[0].get_content() == b"img:64"
    assert result[0].attributes == {"resized": "64"}


def test_pfp_flow_task_runs_through_continuous_flow_executor(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "from pathlib import Path\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = Path(payload['flowfile']['content_path']).read_text()\n"
        "width = payload['task_config']['width']\n"
        "attrs = dict(payload['flowfile'].get('attributes') or {})\n"
        "attrs['resized'] = str(width)\n"
        "pfp.result(flowfiles=[pfp.flowfile(f'{content}:{width}', attrs)])\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, pfp_runtime
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    def _invoke(self, request):
        assert request["context"]["agent_name"] == "agentA"
        payload = request["payload"]
        content = payload["flowfile"]["_content_bytes"].decode("utf-8")
        width = payload["task_config"]["width"]
        attrs = dict(payload["flowfile"].get("attributes") or {})
        attrs["resized"] = str(width)
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=f"{content}:{width}".encode("utf-8"), attributes=attrs))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    flow = FlowParser.parse({
        "id": "pfp-flow-task-e2e",
        "name": "PFP Flow Task E2E",
        "version": "1.0.0",
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 96, "relay": "relay1"},
            },
        },
        "relations": [],
        "entries": [],
        "exits": [],
        "parameters": {},
        "variables": {},
        "groups": {},
    })
    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=[FlowFile(content=b"img", attributes={"source": "test"})],
        max_retries=1,
        timeout=5,
        runtime_context={
            "user_id": "alice",
            "conversation_id": "conv1",
            "scope": "user",
            "agent_name": "agentA",
        },
    )

    assert result.success is True
    assert result.errors == []
    assert len(result.output_flowfiles) == 1
    assert result.output_flowfiles[0].get_content() == b"img:96"
    assert result.output_flowfiles[0].attributes == {"source": "test", "resized": "96"}


def test_pfp_flow_task_relay_can_come_from_flow_parameter_override(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, pfp_runtime
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    calls = []

    def _invoke(self, request):
        calls.append(request)
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=b"out"))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    flow = FlowParser.parse({
        "id": "pfp-flow-task-relay-param",
        "name": "PFP Flow Task Relay Param",
        "version": "1.0.0",
        "parameters": {"relay": "relay-default"},
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 96, "relay": "${relay}"},
            },
        },
        "relations": [],
        "entries": [],
        "exits": [],
    })

    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=[FlowFile(content=b"img")],
        parameters={"relay": "relay-override"},
        max_retries=1,
        timeout=5,
        runtime_context={"user_id": "alice", "conversation_id": "conv1", "scope": "user"},
    )

    assert result.success is True
    assert calls[0]["context"]["relay_id"] == "relay-override"
    assert calls[0]["context"]["user_id"] == "alice"
    assert calls[0]["context"]["conversation_id"] == "conv1"


def test_pfp_flow_tasks_can_use_distinct_flow_relay_parameters(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, pfp_runtime
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    calls = []

    def _invoke(self, request):
        calls.append(request)
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=b"step"))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    flow = FlowParser.parse({
        "id": "pfp-flow-task-multi-relay-param",
        "name": "PFP Flow Task Multi Relay Param",
        "version": "1.0.0",
        "parameters": {
            "relay_a": "relay-default-a",
            "relay_b": "relay-default-b",
            "relay_c": "relay-default-c",
        },
        "tasks": {
            "step_a": {
                "type": "packageResizeImage",
                "parameters": {"width": 64, "relay": "${relay_a}"},
            },
            "step_b": {
                "type": "packageResizeImage",
                "parameters": {"width": 96, "relay": "${relay_b}"},
            },
            "step_c": {
                "type": "packageResizeImage",
                "parameters": {"width": 128, "relay": "${relay_c}"},
            },
        },
        "relations": [
            {"from": "step_a", "to": "step_b", "type": "success"},
            {"from": "step_b", "to": "step_c", "type": "success"},
        ],
        "entries": ["step_a"],
        "exits": ["step_c"],
    })

    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=[FlowFile(content=b"img")],
        parameters={
            "relay_a": "relay-host-a",
            "relay_b": "relay-host-b",
            "relay_c": "relay-host-c",
        },
        max_retries=1,
        timeout=5,
        runtime_context={"user_id": "alice", "conversation_id": "conv1", "scope": "user"},
    )

    assert result.success is True
    assert [call["context"]["relay_id"] for call in calls] == [
        "relay-host-a",
        "relay-host-b",
        "relay-host-c",
    ]
    assert [call["payload"]["task_config"]["width"] for call in calls] == [64, 96, 128]


def test_pfp_package_installs_and_runs_flow_with_packaged_resources(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True, include_flow_task=True,
        flow_task_runner="python")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "from pathlib import Path\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = Path(payload['flowfile']['content_path']).read_text()\n"
        "width = payload['task_config']['width']\n"
        "pfp.result(flowfiles=[pfp.flowfile(f'{content}:{width}', {'flow': 'installed'})])\n",
        encoding="utf-8",
    )
    flow_dir = pkgdir / "content" / "flows"
    flow_dir.mkdir(parents=True)
    flow_data = {
        "id": "packaged-resize-flow",
        "name": "Packaged Resize Flow",
        "version": "1.0.0",
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 128, "relay": "relay1"},
            },
        },
        "relations": [],
        "entries": [],
        "exits": [],
        "parameters": {},
        "variables": {},
        "groups": {},
    }
    (flow_dir / "resize.json").write_text(json.dumps(flow_data), encoding="utf-8")
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["objects"].append({
        "id": "flow:resize-demo",
        "type": "flow",
        "name": "community.wavespeed.resize-demo:1.0.0",
        "fqn": "community.wavespeed.resize-demo:1.0.0",
        "path": "content/flows/resize.json",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    installed = pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["service_provider:image", "flow_task:resize-image", "flow:resize-demo"],
        force=True)
    assert installed["ok"] is True
    assert [item["id"] for item in installed["installed"]] == [
        "service_provider:image",
        "flow_task:resize-image",
        "flow:resize-demo",
    ]

    from core import FlowFile, pfp_runtime
    from core.repository import ScopedRepository
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    def _invoke(self, request):
        assert request["context"]["user_id"] == "alice"
        assert request["context"]["conversation_id"] == "conv1"
        payload = request["payload"]
        content = payload["flowfile"]["_content_bytes"].decode("utf-8")
        width = payload["task_config"]["width"]
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=f"{content}:{width}".encode("utf-8"), attributes={"flow": "installed"}))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)

    service = ServiceRegistry.get_instance().get_live_instance(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    assert service is not None
    assert service.status()["object_id"] == "service_provider:image"

    stored_flow = ScopedRepository.instance().get_flow(
        "community.wavespeed.resize-demo:1.0.0", "user", user_id="alice")
    assert stored_flow["installed_from"]["object_id"] == "flow:resize-demo"
    from tasks.ai.actions.agent_resource import _get_flow_templates_cached
    templates = _get_flow_templates_cached("alice")
    assert any(t["id"] == "packaged-resize-flow" for t in templates)
    result = ContinuousFlowExecutor.run_batch(
        FlowParser.parse(stored_flow),
        input_flowfiles=[FlowFile(content=b"img")],
        max_retries=1,
        timeout=5,
        runtime_context={"user_id": "alice", "conversation_id": "conv1", "scope": "user"},
    )

    assert result.success is True
    assert result.errors == []
    assert len(result.output_flowfiles) == 1
    assert result.output_flowfiles[0].get_content() == b"img:128"
    assert result.output_flowfiles[0].attributes == {"flow": "installed"}


def test_pfp_package_prefills_flow_task_relay_from_conversation_default(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    flow_dir = pkgdir / "content" / "flows"
    flow_dir.mkdir(parents=True)
    (flow_dir / "resize.json").write_text(json.dumps({
        "id": "packaged-resize-flow",
        "name": "Packaged Resize Flow",
        "version": "1.0.0",
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 128},
            },
        },
        "relations": [],
    }), encoding="utf-8")
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["objects"].append({
        "id": "flow:resize-demo",
        "type": "flow",
        "name": "community.wavespeed.resize-demo:1.0.0",
        "fqn": "community.wavespeed.resize-demo:1.0.0",
        "path": "content/flows/resize.json",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    import core.relay_bindings as relay_bindings
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay-conv")

    installed = pfp_package.install_pfp(
        built["path"], user_id="alice", conversation_id="conv1", scope="conversation",
        include=["flow_task:resize-image", "flow:resize-demo"], force=True)
    assert installed["ok"] is True

    from core.repository import ScopedRepository
    stored_flow = ScopedRepository.instance().get_flow(
        "community.wavespeed.resize-demo:1.0.0", "conv", user_id="alice", conv_id="conv1")
    assert stored_flow["tasks"]["resize"]["parameters"]["relay"] == "relay-conv"
    assert stored_flow["tasks"]["resize"]["parameters"]["width"] == 128


def test_pfp_package_prefills_flow_task_relay_from_agent_default(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python")
    flow_dir = pkgdir / "content" / "flows"
    flow_dir.mkdir(parents=True)
    (flow_dir / "resize.json").write_text(json.dumps({
        "id": "packaged-resize-flow",
        "name": "Packaged Resize Flow",
        "version": "1.0.0",
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 128},
            },
        },
        "relations": [],
    }), encoding="utf-8")
    manifest_path = pkgdir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["objects"].append({
        "id": "flow:resize-demo",
        "type": "flow",
        "name": "community.wavespeed.resize-demo:1.0.0",
        "fqn": "community.wavespeed.resize-demo:1.0.0",
        "path": "content/flows/resize.json",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    import core.relay_bindings as relay_bindings
    monkeypatch.setattr(
        relay_bindings, "get_default",
        lambda cid, agent="": "relay-agent" if agent == "agentA" else "relay-conv")

    installed = pfp_package.install_pfp(
        built["path"], user_id="alice", conversation_id="conv1", scope="conversation",
        include=["flow_task:resize-image", "flow:resize-demo"], force=True,
        agent_name="agentA")
    assert installed["ok"] is True

    from core.repository import ScopedRepository
    stored_flow = ScopedRepository.instance().get_flow(
        "community.wavespeed.resize-demo:1.0.0", "conv", user_id="alice", conv_id="conv1")
    assert stored_flow["tasks"]["resize"]["parameters"]["relay"] == "relay-agent"


def test_pfp_package_prefills_flow_task_relay_from_metadata_task_type(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = tmp_path / "metadata-task.pfpdir"
    flow_task_dir = pkgdir / "content" / "flow-tasks" / "upper"
    flow_dir = pkgdir / "content" / "flows"
    flow_task_dir.mkdir(parents=True)
    flow_dir.mkdir(parents=True)
    (flow_task_dir / "task.json").write_text(json.dumps({
        "type": "metadataUpperTask",
        "name": "Metadata Upper",
        "parameters": {"suffix": {"type": "string"}},
    }), encoding="utf-8")
    (flow_dir / "demo.json").write_text(json.dumps({
        "id": "metadata-demo",
        "name": "Metadata Demo",
        "version": "1.0.0",
        "tasks": {
            "upper": {
                "type": "metadataUpperTask",
                "parameters": {"suffix": "!"},
            },
        },
        "relations": [],
    }), encoding="utf-8")
    (pkgdir / "pfp.json").write_text(json.dumps({
        "format": "pawflow.package.v1",
        "package": "community.metadata",
        "version": "1.0.0",
        "description": "Metadata task type package",
        "developer": {
            "email": "dev@example.com",
            "public_key": keypair["public_key"],
        },
        "origin": {"source": "local-test"},
        "objects": [
            {
                "id": "flow_task:upper",
                "type": "flow_task",
                "name": "upper",
                "runner": "python",
                "path": "content/flow-tasks/upper/task.json",
            },
            {
                "id": "flow:demo",
                "type": "flow",
                "name": "community.metadata.demo:1.0.0",
                "fqn": "community.metadata.demo:1.0.0",
                "path": "content/flows/demo.json",
            },
        ],
    }), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    import core.relay_bindings as relay_bindings
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay-conv")

    installed = pfp_package.install_pfp(
        built["path"], user_id="alice", conversation_id="conv1",
        scope="conversation", include=["flow_task:upper", "flow:demo"],
        force=True)
    assert installed["ok"] is True

    from core.repository import ScopedRepository
    stored_flow = ScopedRepository.instance().get_flow(
        "community.metadata.demo:1.0.0", "conv", user_id="alice", conv_id="conv1")
    assert stored_flow["tasks"]["upper"]["parameters"] == {
        "suffix": "!",
        "relay": "relay-conv",
    }


def test_pfp_media_artifact_copy_uses_relay_chunk_copy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime

    class _Relay:
        def __init__(self):
            self.copied = []

        def read_file(self, path):
            raise AssertionError(f"read_file must not be used for media artifacts: {path}")

        def copy_file_to_local(self, source, target):
            self.copied.append((source, target))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"artifact-bytes")

    relay = _Relay()
    output_dir = tmp_path / "server-artifacts"
    result = {
        "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
        "ok": True,
        "result": {"artifact": {"path": "image.png"}},
    }

    pfp_runtime.RelayPackageRuntimeBridge()._copy_result_artifacts(
        relay,
        {"context": {
            "output_dir": ".pawflow/pfp/out",
            "server_output_dir": str(output_dir),
        }},
        result,
        ".pawflow/pfp/root",
    )

    assert relay.copied == [(".pawflow/pfp/out/image.png", str(output_dir / "image.png"))]
    assert (output_dir / "image.png").read_bytes() == b"artifact-bytes"


def test_pfp_task_flowfile_result_content_path_uses_relay_chunk_copy(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime

    class _Relay:
        def __init__(self):
            self.copied = []

        def read_file(self, path):
            raise AssertionError(f"read_file must not be used for PFP task flowfiles: {path}")

        def copy_file_to_local(self, source, target):
            self.copied.append((source, target))
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"flowfile-bytes")

    relay = _Relay()
    result = {
        "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
        "ok": True,
        "flowfiles": [{
            "content_path": "out/result.bin",
            "attributes": {"result": "ok"},
        }],
    }

    pfp_runtime.RelayPackageRuntimeBridge()._copy_result_artifacts(
        relay, {"context": {}}, result, ".pawflow/pfp/root")

    assert relay.copied[0][0] == ".pawflow/pfp/root/out/result.bin"
    copied_path = Path(result["flowfiles"][0]["content_path"])
    assert copied_path.exists()
    flowfiles = pfp_runtime._normalize_task_result(result)
    assert flowfiles[0].is_content_on_disk is True
    assert flowfiles[0].get_content() == b"flowfile-bytes"
    assert flowfiles[0].attributes == {"result": "ok"}
    assert copied_path.exists()
    import gc
    del flowfiles
    gc.collect()
    assert not copied_path.exists()


def test_pfp_media_resolver_prefers_conversation_scoped_provider(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
    from services.base_image_generation import BaseImageGenerationService
    from services.tool_relay_service import ToolRelayService

    class FakePfpImageService(BaseImageGenerationService):
        TYPE = "fakePfpImage"

        def _create_connection(self):
            return self

        def _close_connection(self):
            pass

        def generate(self, **kwargs):
            return {"image_bytes": b"x", "content_type": "image/png"}

    monkeypatch.setitem(ServiceFactory._services, "fakePfpImage", FakePfpImageService)
    registry = ServiceRegistry.get_instance()
    registry.install(
        SCOPE_USER, "alice", "pfp-image", "fakePfpImage",
        config={"scope_marker": "user"})
    registry.install(
        SCOPE_CONV, "conv1", "pfp-image", "fakePfpImage",
        config={"scope_marker": "conversation"})

    service, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "image")()

    assert error is None
    assert service is not None
    assert service.config["scope_marker"] == "conversation"


def test_tool_relay_media_resolver_orders_pfp_and_native_by_scope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry
    from services.base_image_generation import BaseImageGenerationService
    from services.tool_relay_service import ToolRelayService

    class NativeUserImage(BaseImageGenerationService):
        TYPE = "nativeUserImageForScopeOrder"

        def _create_connection(self):
            return self

        def _close_connection(self):
            pass

        def generate(self, **kwargs):
            return {"image_url": "native.png"}

    class PfpConversationImage:
        TYPE = "packageRuntime"

        def get_operations(self):
            return {"generate": {}}

        def generate(self, **kwargs):
            return {"image_url": "pfp.png"}

    def _definition(service_id, service_type, scope, config=None):
        return type("_Def", (), {
            "service_id": service_id,
            "service_type": service_type,
            "scope": scope,
            "scope_id": "conv1" if scope == "conv" else "alice",
            "config": config or {},
        })()

    native_def = _definition("native-user", "nativeUserImageForScopeOrder", "user")
    pfp_def = _definition("pfp-conv", "packageRuntime", "conv", {
        "package_runtime": {"provides": ["media.image_generation"]},
        "operations": {"generate": {}},
    })

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "nativeUserImageForScopeOrder":
                return [native_def]
            if service_type == "packageRuntime":
                return [pfp_def]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            if service_id == "native-user":
                return NativeUserImage({})
            if service_id == "pfp-conv":
                return PfpConversationImage()
            return None

    monkeypatch.setitem(ServiceFactory._services, "nativeUserImageForScopeOrder", NativeUserImage)
    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "image")()

    assert error is None
    assert isinstance(service, PfpConversationImage)


def test_tool_relay_wires_media_handlers_to_operation_specific_resolvers(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from services.tool_relay_service import ToolRelayService

    calls = []

    def _fake_resolver(user_id, conversation_id, media_type, required_methods=()):
        def _resolver():
            return None, None
        _resolver.pfp_test_call = (media_type, tuple(required_methods))
        calls.append(_resolver.pfp_test_call)
        return _resolver

    monkeypatch.setattr(
        ToolRelayService, "_make_media_resolver",
        staticmethod(_fake_resolver))

    registry = ToolRelayService({})._get_registry(
        user_id="alice", conversation_id="conv1", agent_name="agentA")
    handlers = {handler.name: handler for handler in registry.list_tools()}

    assert handlers["generate_video"]._service_resolver.pfp_test_call[0] == "video"
    assert handlers["speech_to_video"]._service_resolver.pfp_test_call == (
        "speech_to_video", ("speech_to_video",))
    assert handlers["upscale_image"]._service_resolver.pfp_test_call == (
        "upscale", ("upscale",))
    assert handlers["remove_background"]._service_resolver.pfp_test_call == (
        "upscale", ("remove_background",))
    assert handlers["delete_voice"]._service_resolver.pfp_test_call == (
        "voice", ("delete_voice_id",))
    assert handlers["speak"]._service_resolver.pfp_test_call == (
        "tts", ("speak",))
    assert handlers["get_image_model_info"]._service_resolver.pfp_test_call == (
        "image", ("get_model_info",))


def test_tool_relay_pfp_resolver_requires_exact_operation(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from services.tool_relay_service import ToolRelayService

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_USER, "alice", "pfp-bg", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.media",
            "version": "1.0.0",
            "object_id": "service_provider:bg",
            "provides": ["media.background_removal"],
        },
        "installed_from": {},
        "operations": {"remove_background": {}},
    })
    registry.install(SCOPE_USER, "alice", "pfp-upscale", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.media",
            "version": "1.0.0",
            "object_id": "service_provider:upscale",
            "provides": ["media.image_upscale"],
        },
        "installed_from": {},
        "operations": {"upscale": {}},
    })

    service, error = ToolRelayService._make_media_resolver(
        "alice", "", "upscale", ("upscale",))()

    assert error is None
    assert service is not None
    assert service.config["package_runtime"]["object_id"] == "service_provider:upscale"


def test_video_handler_passes_call_mode_to_auto_resolver(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.handlers.media import VideoGenerationHandler

    seen = []

    class _VideoService:
        def generate(self, **kwargs):
            raise RuntimeError("stop after text mode")

        def image_to_video(self, **kwargs):
            raise RuntimeError("stop after image mode")

    handler = VideoGenerationHandler()

    def _resolver(required_methods=()):
        seen.append(tuple(required_methods))
        return _VideoService(), None

    handler.set_service_resolver(_resolver)

    handler.execute({"prompt": "cat"})
    handler.execute({"prompt": "cat", "image_url": "https://example.test/in.png"})

    assert seen == [
        ("generate",),
        ("image_to_video", "reference_to_video"),
    ]


def test_tool_relay_video_resolver_uses_call_mode_operation(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from services.tool_relay_service import ToolRelayService

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_USER, "alice", "pfp-i2v", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.i2v",
            "version": "1.0.0",
            "object_id": "service_provider:image-video",
            "provides": ["media.video_generation"],
        },
        "installed_from": {},
        "operations": {"image_to_video": {}},
    })
    registry.install(SCOPE_USER, "alice", "pfp-t2v", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.t2v",
            "version": "1.0.0",
            "object_id": "service_provider:text-video",
            "provides": ["media.video_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })

    service, error = ToolRelayService._make_media_resolver(
        "alice", "", "video")(("generate",))

    assert error is None
    assert service is not None
    assert service.config["package_runtime"]["object_id"] == "service_provider:text-video"


def test_agent_video_resolver_uses_call_mode_operation(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.service_registry import ServiceRegistry
    from tasks.ai.agent_utils import AgentUtilsMixin

    def _definition(service_id, operations):
        return type("_Def", (), {
            "service_id": service_id,
            "service_type": "packageRuntime",
            "scope": "user",
            "config": {
                "package_runtime": {"provides": ["media.video_generation"]},
                "operations": operations,
            },
        })()

    class _ImageVideoService:
        def get_operations(self):
            return {"image_to_video": {}}

    class _TextVideoService:
        def get_operations(self):
            return {"generate": {}}

        def generate(self, **kwargs):
            return {"video_url": "generated.mp4"}

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "packageRuntime":
                return [
                    _definition("pfp-i2v", {"image_to_video": {}}),
                    _definition("pfp-t2v", {"generate": {}}),
                ]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            return {
                "pfp-i2v": _ImageVideoService(),
                "pfp-t2v": _TextVideoService(),
            }.get(service_id)

    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = AgentUtilsMixin()._make_video_resolver(
        "alice", "", "agentA")(("generate",))

    assert error is None
    assert isinstance(service, _TextVideoService)


def test_tool_relay_pfp_resolver_uses_exact_definition_when_service_ids_shadow(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
    from services.tool_relay_service import ToolRelayService

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_CONV, "conv1", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.edit",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"edit_image": {}},
    })
    registry.install(SCOPE_USER, "alice", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.generate",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })

    service, error = ToolRelayService._make_media_resolver(
        "alice", "conv1", "image", ("generate",))()

    assert error is None
    assert service is not None
    assert service.config["package_runtime"]["package"] == "pkg.generate"


def test_agent_pfp_resolver_uses_exact_definition_when_service_ids_shadow(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
    from tasks.ai.agent_utils import AgentUtilsMixin

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_CONV, "conv1", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.edit",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"edit_image": {}},
    })
    registry.install(SCOPE_USER, "alice", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.generate",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })

    service, error = AgentUtilsMixin()._make_image_resolver(
        "alice", "conv1", "agentA", required_methods=("generate",))()

    assert error is None
    assert service is not None
    assert service.config["package_runtime"]["package"] == "pkg.generate"


def test_agent_media_resolver_skips_pfp_provider_without_required_method(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.service_registry import ServiceRegistry
    from tasks.ai.agent_utils import AgentUtilsMixin

    def _definition(service_id, operations):
        return type("_Def", (), {
            "service_id": service_id,
            "service_type": "packageRuntime",
            "scope": "user",
            "config": {
                "package_runtime": {"provides": ["media.image_generation"]},
                "operations": operations,
            },
        })()

    class _BadImageService:
        def get_operations(self):
            return {"edit_image": {}}

        def generate(self, **kwargs):
            raise AssertionError("provider without generate operation must be skipped")

    class _GoodImageService:
        def get_operations(self):
            return {"generate": {}}

        def generate(self, **kwargs):
            return {"image_url": "generated.png"}

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "packageRuntime":
                return [
                    _definition("bad-pfp-image", {"edit_image": {}}),
                    _definition("good-pfp-image", {"generate": {}}),
                ]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            return {
                "bad-pfp-image": _BadImageService(),
                "good-pfp-image": _GoodImageService(),
            }.get(service_id)

    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = AgentUtilsMixin()._make_image_resolver(
        "alice", "", "agentA", required_methods=("generate",))()

    assert error is None
    assert isinstance(service, _GoodImageService)


def test_agent_media_resolver_accepts_pfp_native_model_info_method(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.service_registry import ServiceRegistry
    from tasks.ai.agent_utils import AgentUtilsMixin

    definition = type("_Def", (), {
        "service_id": "pfp-image-info",
        "service_type": "packageRuntime",
        "scope": "user",
        "config": {
            "package_runtime": {"provides": ["media.image_generation"]},
            "operations": {"generate": {}},
        },
    })()

    class _PfpImageInfoService:
        def get_operations(self):
            return {"generate": {}}

        def get_model_info(self):
            return {"provider": "pfp"}

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "packageRuntime":
                return [definition]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            if service_id == "pfp-image-info":
                return _PfpImageInfoService()
            return None

    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = AgentUtilsMixin()._make_image_resolver(
        "alice", "", "agentA", required_methods=("get_model_info",))()

    assert error is None
    assert isinstance(service, _PfpImageInfoService)


def test_agent_media_resolver_orders_pfp_and_native_by_scope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry
    from services.base_image_generation import BaseImageGenerationService
    from tasks.ai.agent_utils import AgentUtilsMixin

    class NativeUserImage(BaseImageGenerationService):
        TYPE = "nativeAgentImageForScopeOrder"

        def _create_connection(self):
            return self

        def _close_connection(self):
            pass

        def generate(self, **kwargs):
            return {"image_url": "native.png"}

    class PfpConversationImage:
        def get_operations(self):
            return {"generate": {}}

        def generate(self, **kwargs):
            return {"image_url": "pfp.png"}

    def _definition(service_id, service_type, scope, config=None):
        return type("_Def", (), {
            "service_id": service_id,
            "service_type": service_type,
            "scope": scope,
            "config": config or {},
        })()

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "nativeAgentImageForScopeOrder":
                return [_definition("native-user", service_type, "user")]
            if service_type == "packageRuntime":
                return [_definition("pfp-conv", service_type, "conv", {
                    "package_runtime": {"provides": ["media.image_generation"]},
                    "operations": {"generate": {}},
                })]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            if service_id == "native-user":
                return NativeUserImage({})
            if service_id == "pfp-conv":
                return PfpConversationImage()
            return None

    monkeypatch.setitem(ServiceFactory._services, "nativeAgentImageForScopeOrder", NativeUserImage)
    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = AgentUtilsMixin()._make_image_resolver(
        "alice", "conv1", "agentA", required_methods=("generate",))()

    assert error is None
    assert isinstance(service, PfpConversationImage)


def test_agent_speech_to_video_resolver_orders_lipsync_pfp_by_scope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import ServiceFactory
    from core.service_registry import ServiceRegistry
    from services.base_video_generation import BaseVideoGenerationService
    from tasks.ai.agent_utils import AgentUtilsMixin

    class NativeUserSpeechVideo(BaseVideoGenerationService):
        TYPE = "nativeUserSpeechVideoForScopeOrder"

        def _create_connection(self):
            return self

        def _close_connection(self):
            pass

        def generate(self, **kwargs):
            return {"video_url": "native.mp4"}

        def speech_to_video(self, **kwargs):
            return {"video_url": "native-s2v.mp4"}

    class PfpConversationSpeechVideo:
        def get_operations(self):
            return {"speech_to_video": {}}

        def speech_to_video(self, **kwargs):
            return {"video_url": "pfp-s2v.mp4"}

    def _definition(service_id, service_type, scope, config=None):
        return type("_Def", (), {
            "service_id": service_id,
            "service_type": service_type,
            "scope": scope,
            "config": config or {},
        })()

    class _Registry:
        def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
            if service_type == "nativeUserSpeechVideoForScopeOrder":
                return [_definition("native-user", service_type, "user")]
            if service_type == "packageRuntime":
                return [_definition("pfp-conv", service_type, "conv", {
                    "package_runtime": {"provides": ["media.lipsync"]},
                    "operations": {"speech_to_video": {}},
                })]
            return []

        def resolve(self, service_id, *, user_id="", conv_id=""):
            if service_id == "native-user":
                return NativeUserSpeechVideo({})
            if service_id == "pfp-conv":
                return PfpConversationSpeechVideo()
            return None

    monkeypatch.setitem(
        ServiceFactory._services,
        "nativeUserSpeechVideoForScopeOrder",
        NativeUserSpeechVideo)
    monkeypatch.setattr(ServiceRegistry, "get_instance", staticmethod(lambda: _Registry()))

    service, error = AgentUtilsMixin()._make_speech_to_video_resolver(
        "alice", "conv1", "agentA")()

    assert error is None
    assert isinstance(service, PfpConversationSpeechVideo)


def test_pfp_package_qualified_service_ignores_same_id_other_package_shadow(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core import pfp_runtime
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_CONV, "conv1", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.other",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })
    registry.install(SCOPE_USER, "alice", "image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.target",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })

    service = pfp_runtime._resolve_package_service(
        registry,
        {"package": "pkg.target", "kind": "service", "name": "image"},
        user_id="alice",
        conversation_id="conv1",
    )

    assert service is not None
    assert service.config["package_runtime"]["package"] == "pkg.target"


def test_resource_store_list_all_conversation_overrides_user_tool(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.resource_store import ResourceStore

    store = ResourceStore.instance()
    store.create("tool", "shared_reader", "alice", {
        "name": "shared_reader",
        "description": "user tool",
        "source": "def run(**kwargs):\n    return 'user'\n",
        "package_runtime": {"package": "pkg.user"},
    })
    store.create("tool", "shared_reader", "alice", {
        "name": "shared_reader",
        "description": "conversation tool",
        "source": "def run(**kwargs):\n    return 'conv'\n",
        "package_runtime": {"package": "pkg.conv"},
    }, conversation_id="conv1")

    tools = store.list_all("tool", "alice", conversation_id="conv1")
    shared = [tool for tool in tools if tool.get("name") == "shared_reader"]

    assert len(shared) == 1
    assert shared[0]["_scope"] == "conversation"
    assert shared[0]["package_runtime"]["package"] == "pkg.conv"


def test_service_registry_task_subconversation_inherits_parent_services(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import services.package_runtime_service  # noqa: F401
    from core.service_registry import ServiceRegistry, SCOPE_CONV

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_CONV, "conv1", "pfp-image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.parent",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })

    service = registry.resolve(
        "pfp-image", user_id="alice", conv_id="conv1::task::t_123")
    delegate_service = registry.resolve(
        "pfp-image", user_id="alice", conv_id="conv1::delegate::agent")

    assert service is not None
    assert service.config["package_runtime"]["package"] == "pkg.parent"
    assert delegate_service is not None
    assert delegate_service.config["package_runtime"]["package"] == "pkg.parent"


def test_task_subconversation_inherits_parent_relay_binding(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.conversation_store import ConversationStore
    from core import relay_bindings

    ConversationStore.instance().save(
        "conv1", [{"role": "user", "content": "hello"}], user_id="alice")
    assert relay_bindings.link_relay("conv1", "relay-main") is True

    assert relay_bindings.get_default("conv1::task::t_123") == "relay-main"
    assert relay_bindings.get_linked("conv1::task::t_123") == ["relay-main"]
    assert relay_bindings.get_default("conv1::delegate::agent") == "relay-main"
    assert relay_bindings.get_linked("conv1::delegate::agent") == ["relay-main"]


def test_task_verify_subconversation_inherits_parent_services_and_relay(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    import time
    import uuid
    import services.package_runtime_service  # noqa: F401
    from core import relay_bindings
    from core.conversation_store import ConversationStore
    from core.service_registry import ServiceRegistry, SCOPE_CONV

    registry = ServiceRegistry.get_instance()
    registry.install(SCOPE_CONV, "conv1", "pfp-image", "packageRuntime", config={
        "package_runtime": {
            "package": "pkg.parent",
            "version": "1.0.0",
            "object_id": "service_provider:image",
            "provides": ["media.image_generation"],
        },
        "installed_from": {},
        "operations": {"generate": {}},
    })
    ConversationStore.instance().save("conv1", [{
        "role": "user",
        "content": "hello",
        "msg_id": str(uuid.uuid4()),
        "timestamp": time.time(),
    }], user_id="alice")
    assert relay_bindings.link_relay("conv1", "relay-main") is True

    service = registry.resolve(
        "pfp-image", user_id="alice", conv_id="conv1::task_verify::t_123")

    assert service is not None
    assert service.config["package_runtime"]["package"] == "pkg.parent"
    assert relay_bindings.get_default("conv1::task_verify::t_123") == "relay-main"


def test_task_verify_subconversation_inherits_parent_dynamic_tools(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.resource_store import ResourceStore
    from services.tool_relay_service import ToolRelayService

    ResourceStore.instance().create("tool", "conv_package_tool", "alice", {
        "name": "conv_package_tool",
        "description": "conversation package tool",
        "source": "",
        "package_runtime": {
            "package": "pkg.parent",
            "object_id": "tool:conv_package_tool",
        },
        "installed_from": {},
    }, conversation_id="conv1")

    registry = ToolRelayService({})._get_registry(
        user_id="alice", conversation_id="conv1::task_verify::t_123",
        agent_name="agentA")

    assert registry.get("conv_package_tool") is not None


def test_package_capability_broker_task_subconversation_reads_parent_install(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, package_id="pkg.provider",
        include_service_provider=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", conversation_id="conv1",
        scope="conversation", include=["service_provider:image"], force=True)

    from core.pfp_capabilities import PackageCapabilityBroker
    caller = {
        "package": "pkg.consumer",
        "object_id": "tool:caller",
        "allowed_services": ["pkg.provider/service:image"],
    }

    target = PackageCapabilityBroker(
        user_id="alice", conversation_id="conv1::task_verify::t_123",
        scope="conversation",
    ).authorize_service_call(caller, "pkg.provider/service:image")["target"]

    assert target == {
        "kind": "service",
        "name": "image",
        "package": "pkg.provider",
        "version": "",
    }


def test_package_capability_broker_prefers_exact_subconversation_install(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    parent_dir = _write_package_dir(
        tmp_path / "parent", keypair, package_id="pkg.provider",
        version="1.0.0", include_service_provider=True)
    child_dir = _write_package_dir(
        tmp_path / "child", keypair, package_id="pkg.provider",
        version="2.0.0", include_service_provider=True)

    for pkgdir, service_id in ((parent_dir, "parent-provider"), (child_dir, "child-provider")):
        manifest_path = pkgdir / "pfp.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for obj in manifest["objects"]:
            if obj.get("id") == "service_provider:image":
                obj["name"] = service_id
                obj["service_id"] = service_id
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    parent_built = pfp_package.build_pfp(str(parent_dir), private_key=keypair["private_key"])
    child_built = pfp_package.build_pfp(str(child_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        parent_built["path"], user_id="alice", conversation_id="conv1",
        scope="conversation", include=["service_provider:image"], force=True)
    pfp_package.install_pfp(
        child_built["path"], user_id="alice", conversation_id="conv1::task::t_123",
        scope="conversation", include=["service_provider:image"], force=True)

    from core.pfp_capabilities import PackageCapabilityBroker
    caller = {
        "package": "pkg.consumer",
        "object_id": "tool:caller",
        "allowed_services": ["pkg.provider@2.0.0/service:image"],
    }

    target = PackageCapabilityBroker(
        user_id="alice", conversation_id="conv1::task::t_123",
        scope="conversation",
    ).authorize_service_call(caller, "pkg.provider@2.0.0/service:image")["target"]

    assert target["version"] == "2.0.0"


def test_package_capability_broker_propagates_grant_version_to_dispatch(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    v1_dir = _write_package_dir(
        tmp_path / "v1", keypair, package_id="pkg.mix", version="1.0.0",
        include_service_provider=True)
    v2_dir = _write_package_dir(
        tmp_path / "v2", keypair, package_id="pkg.mix", version="2.0.0",
        include_service_provider=True)
    v1_built = pfp_package.build_pfp(str(v1_dir), private_key=keypair["private_key"])
    v2_built = pfp_package.build_pfp(str(v2_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        v1_built["path"], user_id="alice", include=["tool:reader"], force=True)
    pfp_package.install_pfp(
        v2_built["path"], user_id="alice", include=["service_provider:image"], force=True)

    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError
    from core.tool_registry import ToolRegistry

    def _invoke_tool(runtime, installed_from, arguments, context):
        raise AssertionError(f"unexpected runtime dispatch: {runtime.get('version')}")

    monkeypatch.setattr(pfp_runtime, "invoke_tool", _invoke_tool)
    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "pkg.consumer",
            "object_id": "tool:caller",
            "allowed_tools": ["pkg.mix@2.0.0/tool:reader"],
        },
        tool_registry=ToolRegistry(),
    )

    try:
        host.execute_tool_call("pkg.mix/tool:reader", {})
    except PackageCapabilityError as exc:
        assert "version mismatch" in str(exc)
    else:
        raise AssertionError("unversioned call used the stale package runtime")


def test_pfp_inspect_task_subconversation_reads_parent_package_dependencies(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    provider_dir = _write_package_dir(
        tmp_path / "provider", keypair, package_id="pkg.provider")
    provider_built = pfp_package.build_pfp(str(provider_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        provider_built["path"], user_id="alice", conversation_id="conv1",
        scope="conversation", include=["tool:reader"], force=True)

    consumer_dir = _write_package_dir(
        tmp_path / "consumer", keypair, package_id="pkg.consumer",
        dependencies=[{
            "package": "pkg.provider",
            "version": "1.0.0",
            "object": "tool:reader",
        }])
    consumer_built = pfp_package.build_pfp(str(consumer_dir), private_key=keypair["private_key"])

    inspected = pfp_package.inspect_pfp(
        consumer_built["path"], user_id="alice",
        conversation_id="conv1::task::t_1", scope="conversation")

    reader = next(row for row in inspected["objects"] if row["id"] == "tool:reader")
    assert reader["status"] != "missing_dependency"


def test_pfp_inspect_dependency_checks_installed_object_version(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    v1_dir = _write_package_dir(
        tmp_path / "v1", keypair, package_id="pkg.mix", version="1.0.0",
        include_service_provider=True)
    v2_dir = _write_package_dir(
        tmp_path / "v2", keypair, package_id="pkg.mix", version="2.0.0",
        include_service_provider=True)
    v1_built = pfp_package.build_pfp(str(v1_dir), private_key=keypair["private_key"])
    v2_built = pfp_package.build_pfp(str(v2_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        v1_built["path"], user_id="alice", include=["tool:reader"], force=True)
    pfp_package.install_pfp(
        v2_built["path"], user_id="alice", include=["service_provider:image"], force=True)

    consumer_dir = _write_package_dir(
        tmp_path / "consumer", keypair, package_id="pkg.consumer",
        dependencies=[{
            "package": "pkg.mix",
            "version": "2.0.0",
            "object": "tool:reader",
        }])
    manifest_path = consumer_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for obj in manifest["objects"]:
        if obj["id"] == "tool:reader":
            obj["id"] = "tool:consumer_reader"
            obj["name"] = "consumer_reader"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    consumer_built = pfp_package.build_pfp(str(consumer_dir), private_key=keypair["private_key"])

    inspected = pfp_package.inspect_pfp(consumer_built["path"], user_id="alice")
    reader = next(row for row in inspected["objects"] if row["id"] == "tool:consumer_reader")
    assert reader["status"] == "missing_dependency"
    assert reader["missing_dependencies"] == [{
        "package": "pkg.mix",
        "version": "2.0.0",
        "object": "tool:reader",
    }]


def test_pfp_flow_task_runtime_resolves_parent_for_task_subconversation(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True, flow_task_runner="python")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", conversation_id="conv1",
        scope="conversation", include=["flow_task:resize-image"], force=True)

    resolved = pfp_package.resolve_installed_flow_task_runtime(
        "packageResizeImage", user_id="alice",
        conversation_id="conv1::task::t_123", scope="conversation")

    assert resolved["package_runtime"]["package"] == "community.wavespeed"


def test_package_qualified_tool_resolves_shadowed_user_tool_from_store(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.conversation_store import ConversationStore
    from core.resource_store import ResourceStore
    from core.tool_mcp_filters import set_filters
    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry

    store = ResourceStore.instance()
    store.create("tool", "shared_tool", "alice", {
        "name": "shared_tool",
        "description": "user package tool",
        "source": "",
        "package_runtime": {"package": "pkg.user", "object_id": "tool:shared_tool"},
        "installed_from": {},
    })
    store.create("tool", "shared_tool", "alice", {
        "name": "shared_tool",
        "description": "conversation package tool",
        "source": "",
        "package_runtime": {"package": "pkg.conv", "object_id": "tool:shared_tool"},
        "installed_from": {},
    }, conversation_id="conv1")
    ConversationStore.instance().save("conv1", [], user_id="alice")
    set_filters("conv1", {"enabled_dynamic_tools": ["shared_tool"]})
    registry = ToolRegistry()
    load_tools_into_registry(registry, "alice", "conv1")

    handler = pfp_runtime._resolve_package_tool(
        registry,
        {"package": "pkg.user", "kind": "tool", "name": "shared_tool"},
        user_id="alice",
        conversation_id="conv1",
    )

    assert handler is not None
    assert handler._package_runtime["package"] == "pkg.user"


def test_package_qualified_tool_fallback_respects_dynamic_tool_filters(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.resource_store import ResourceStore
    from core.tool_registry import ToolRegistry

    store = ResourceStore.instance()
    store.create("tool", "user_only_tool", "alice", {
        "name": "user_only_tool",
        "description": "user package tool",
        "source": "",
        "package_runtime": {"package": "pkg.user", "object_id": "tool:user_only_tool"},
        "installed_from": {},
    })
    registry = ToolRegistry()

    handler = pfp_runtime._resolve_package_tool(
        registry,
        {"package": "pkg.user", "kind": "tool", "name": "user_only_tool"},
        user_id="alice",
        conversation_id="conv1",
    )

    assert handler is None


def test_pfp_flow_task_proxy_resolves_runtime_by_user_scope(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    alice_key = pfp_package.create_signing_key()
    bob_key = pfp_package.create_signing_key()
    alice_pkg = _write_package_dir(
        tmp_path / "alice", alice_key, package_id="community.alice",
        include_flow_task=True, flow_task_runner="python")
    bob_pkg = _write_package_dir(
        tmp_path / "bob", bob_key, package_id="community.bob",
        include_flow_task=True, flow_task_runner="python")
    alice_built = pfp_package.build_pfp(str(alice_pkg), private_key=alice_key["private_key"])
    bob_built = pfp_package.build_pfp(str(bob_pkg), private_key=bob_key["private_key"])
    pfp_package.install_pfp(
        alice_built["path"], user_id="alice",
        include=["flow_task:resize-image"], force=True)
    pfp_package.install_pfp(
        bob_built["path"], user_id="bob",
        include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime
    task_cls = TaskFactory.get("packageResizeImage")
    task_cls.PACKAGE_RUNTIME = {**task_cls.PACKAGE_RUNTIME, "package": "stale.global.proxy"}

    def _invoke(self, request):
        assert request["package"]["package"] == "community.alice"
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=b"alice-runtime"))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    result = task_cls({
        "width": 64,
        "relay": "relay1",
        "_user_id": "alice",
        "_conversation_id": "conv1",
        "_scope": "user",
    }).execute(FlowFile(content=b"in"))

    assert result[0].get_content() == b"alice-runtime"


def test_pfp_flow_task_user_scope_runs_without_conversation_id(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True, flow_task_runner="python")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory, pfp_runtime
    task_cls = TaskFactory.get("packageResizeImage")

    def _invoke(self, request):
        assert request["context"]["user_id"] == "alice"
        assert request["context"]["conversation_id"] == ""
        assert request["context"]["scope"] == "user"
        return {
            "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
            "ok": True,
            "flowfiles": [pfp_runtime._flowfile_descriptor(
                FlowFile(content=b"user-scope"))],
        }

    monkeypatch.setattr(pfp_runtime.RelayPackageRuntimeBridge, "invoke", _invoke)
    result = task_cls({
        "width": 64,
        "relay": "relay1",
        "_user_id": "alice",
        "_scope": "user",
    }).execute(FlowFile(content=b"in"))

    assert result[0].get_content() == b"user-scope"


def test_pfp_reload_all_installed_flow_tasks(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import TaskFactory
    TaskFactory._tasks.pop("packageResizeImage", None)
    loaded = pfp_package.load_all_installed_package_tasks()

    assert loaded["ok"] is True
    assert loaded["loaded"] == [{
        "package": "community.wavespeed",
        "object_id": "flow_task:resize-image",
        "task_type": "packageResizeImage",
        "scope": "user",
        "user_id": "alice",
        "conversation_id": "",
    }]
    assert TaskFactory.get("packageResizeImage").PACKAGE_RUNTIME["package"] == "community.wavespeed"


def test_pfp_runtime_resources_reload_after_registry_reset(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True, include_flow_task=True)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice",
        include=["tool:reader", "service_provider:image", "flow_task:resize-image"],
        force=True)

    from core import TaskFactory
    from core.resource_store import ResourceStore
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry

    ResourceStore.reset()
    ServiceRegistry.reset()
    TaskFactory._tasks.pop("packageResizeImage", None)

    registry = ToolRegistry()
    assert load_tools_into_registry(registry, "alice") == 1
    assert getattr(registry.get("reader"), "_is_pfp_tool", False) is True

    service_registry = ServiceRegistry.get_instance()
    live = service_registry.get_live_instance(
        SCOPE_USER, "alice", "wavespeed-image-provider")
    assert live is not None
    assert live.is_connected() is True
    assert live.status()["object_id"] == "service_provider:image"

    loaded = pfp_package.load_installed_package_tasks(user_id="alice")
    assert loaded["loaded"][0]["task_type"] == "packageResizeImage"
    assert TaskFactory.get("packageResizeImage").PACKAGE_RUNTIME["object_id"] == "flow_task:resize-image"


def test_pfp_plan_blocks_missing_package_dependency(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair,
        dependencies=[{"package": "community.base", "version": "1.0.0"}],
        tool_allowed_tools=[{"package": "community.base", "object": "tool:reader"}],
    )

    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built["path"], user_id="alice")

    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:pkg-skill")
    assert skill_row["status"] == "missing_dependency"
    assert skill_row["selected"] is False
    assert skill_row["missing_dependencies"] == [{"package": "community.base", "version": "1.0.0"}]

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert result["installed"] == []
    assert {"id": "skill:pkg-skill", "reason": "missing_dependency"} in result["skipped"]


def test_pfp_plan_accepts_installed_package_dependency(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill",
        include_service_provider=True)
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice",
        include=["skill:base-skill", "tool:reader", "service_provider:image"],
        force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        dependencies=[{"package": "community.base", "version": "1.0.0"}],
        tool_allowed_tools=[{"package": "community.base", "object": "tool:reader"}],
    )
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(dependent["path"], user_id="alice")

    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:pkg-skill")
    assert skill_row["status"] == "new"
    assert skill_row["selected"] is True
    assert skill_row["missing_dependencies"] == []


def test_pfp_plan_accepts_service_alias_for_installed_service_provider_dependency(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", include_service_provider=True)
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice",
        include=["service_provider:image"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        package_id="community.dependent")
    manifest_path = dependent_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tool = next(obj for obj in manifest["objects"] if obj["id"] == "tool:reader")
    tool["allowed_tools"] = []
    tool["allowed_services"] = [{
        "package": "community.base",
        "object": "service:image",
    }]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dependent = pfp_package.build_pfp(
        str(dependent_dir), private_key=keypair["private_key"])

    plan = pfp_package.inspect_pfp(dependent["path"], user_id="alice")
    tool_row = next(row for row in plan["objects"] if row["id"] == "tool:reader")
    assert tool_row["status"] == "new"
    assert tool_row["missing_dependencies"] == []

    installed = pfp_package.install_pfp(
        dependent["path"], user_id="alice",
        include=["tool:reader"], force=True)
    assert installed["ok"] is True
    assert [row["id"] for row in installed["installed"]] == ["tool:reader"]


def test_pfp_plan_accepts_installed_package_dependency_range(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair, version="1.5.0",
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill", "tool:reader"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        dependencies=[{"package": "community.base", "version": ">=1.0.0,<2.0.0"}],
        tool_allowed_tools=[{"package": "community.base", "object": "tool:reader"}],
    )
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(dependent["path"], user_id="alice")

    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:pkg-skill")
    assert skill_row["status"] == "new"
    assert skill_row["missing_dependencies"] == []


def test_pfp_uninstall_blocks_installed_dependents_without_force(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        dependencies=[{"package": "community.base", "version": "1.0.0"}],
    )
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        dependent["path"], user_id="alice", include=["skill:pkg-skill"], force=True)

    blocked = pfp_package.uninstall_pfp("community.base", user_id="alice")
    assert blocked["ok"] is False
    assert blocked["removed"] == []
    assert blocked["blocked_by"] == [{"package": "community.wavespeed", "version": "1.0.0"}]

    installed = pfp_package.list_installed_packages(user_id="alice")
    base_row = next(row for row in installed["packages"] if row["package"] == "community.base")
    assert base_row["blocked_by"] == [{"package": "community.wavespeed", "version": "1.0.0"}]

    forced = pfp_package.uninstall_pfp("community.base", user_id="alice", force=True)
    assert forced["ok"] is True
    assert forced["removed"][0]["object_id"] == "skill:base-skill"


def test_pfp_uninstall_blocks_dependents_from_package_grants(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["tool:reader"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        package_id="community.consumer", include_service_provider=True)
    manifest_path = dependent_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = next(obj for obj in manifest["objects"] if obj["id"] == "service_provider:image")
    provider["allowed_tools"] = [{"package": "community.base", "object": "tool:reader"}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        dependent["path"], user_id="alice", include=["service_provider:image"], force=True)

    blocked = pfp_package.uninstall_pfp("community.base", user_id="alice")
    assert blocked["ok"] is False
    assert blocked["removed"] == []
    assert blocked["blocked_by"] == [{"package": "community.consumer", "version": "1.0.0"}]


def test_pfp_uninstall_user_package_blocks_conversation_dependents(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["tool:reader"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        package_id="community.consumer", include_service_provider=True)
    manifest_path = dependent_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = next(obj for obj in manifest["objects"] if obj["id"] == "service_provider:image")
    provider["allowed_tools"] = [{"package": "community.base", "object": "tool:reader"}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        dependent["path"], user_id="alice", conversation_id="conv-1",
        scope="conversation", include=["service_provider:image"], force=True)

    blocked = pfp_package.uninstall_pfp("community.base", user_id="alice")
    assert blocked["ok"] is False
    assert blocked["removed"] == []
    assert blocked["blocked_by"] == [{
        "package": "community.consumer",
        "version": "1.0.0",
        "scope": "conversation",
        "conversation_id": "conv-1",
    }]


def test_pfp_plan_blocks_missing_package_object_dependency(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        tool_allowed_tools=[{"package": "community.base", "object": "tool:reader"}],
    )
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(dependent["path"], user_id="alice")

    tool_row = next(row for row in plan["objects"] if row["id"] == "tool:reader")
    assert tool_row["status"] == "missing_dependency"
    assert tool_row["missing_dependencies"] == [{
        "package": "community.base", "version": "", "object": "tool:reader",
    }]


def test_pfp_capability_broker_authorizes_declared_builtin_and_package_refs(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill",
        include_service_provider=True)
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice",
        include=["skill:base-skill", "tool:reader", "service_provider:image"],
        force=True)

    from core.pfp_capabilities import PackageCapabilityBroker, PackageCapabilityError
    broker = PackageCapabilityBroker(user_id="alice")
    runtime = {
        "package": "community.consumer",
        "object_id": "tool:consumer",
        "allowed_tools": [
            {"name": "read"},
            {"package": "community.base", "version": ">=1.0.0,<2.0.0", "object": "tool:reader"},
        ],
        "allowed_services": [
            {"package": "community.base", "version": ">=1.0.0,<2.0.0", "object": "service:image"},
        ],
    }

    builtin = broker.authorize_tool_call(runtime, "read")
    assert builtin["target"] == {"kind": "tool", "name": "read", "package": "", "version": ""}

    packaged = broker.authorize_tool_call(runtime, "community.base/tool:reader")
    assert packaged["target"]["package"] == "community.base"
    assert packaged["target"]["name"] == "reader"

    service = broker.authorize_service_call(runtime, "community.base/service:image")
    assert service["target"]["package"] == "community.base"
    assert service["target"]["name"] == "image"

    try:
        broker.authorize_tool_call(runtime, "bash")
    except PackageCapabilityError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("undeclared builtin call should be denied")


def test_pfp_capability_broker_requires_referenced_package_installed(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.pfp_capabilities import PackageCapabilityBroker, PackageCapabilityError

    broker = PackageCapabilityBroker(user_id="alice")
    runtime = {
        "package": "community.consumer",
        "object_id": "tool:consumer",
        "allowed_tools": [
            {"package": "community.missing", "object": "tool:reader"},
        ],
    }

    try:
        broker.authorize_tool_call(runtime, "community.missing/tool:reader")
    except PackageCapabilityError as exc:
        assert "not installed" in str(exc)
    else:
        raise AssertionError("missing package dependency should be denied")


def test_pfp_capability_broker_requires_referenced_object_installed(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill"], force=True)

    from core.pfp_capabilities import PackageCapabilityBroker, PackageCapabilityError
    broker = PackageCapabilityBroker(user_id="alice")
    runtime = {
        "package": "community.consumer",
        "object_id": "tool:consumer",
        "allowed_tools": [
            {"package": "community.base", "object": "tool:reader"},
        ],
    }

    try:
        broker.authorize_tool_call(runtime, "community.base/tool:reader")
    except PackageCapabilityError as exc:
        assert "object is not installed" in str(exc)
    else:
        raise AssertionError("missing package object dependency should be denied")


def test_pfp_signature_tamper_is_rejected(tmp_path):
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    tampered = tmp_path / "tampered.pfp"
    with zipfile.ZipFile(built["path"], "r") as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "content/skills/pkg-skill/SKILL.md":
                data = data + b"tamper"
            dst.writestr(name, data)

    try:
        pfp_package.inspect_pfp(str(tampered), user_id="alice")
    except pfp_package.PfpError as exc:
        assert "hashes" in str(exc)
    else:
        raise AssertionError("tampered package should be rejected")


_UNSAFE_PATH_INPUTS = [
    # Empty / whitespace
    "",
    "   ",
    "\t\n",
    # Absolute paths
    "/etc/passwd",
    "//etc/passwd",
    "\\windows\\system32",
    "C:/Windows/System32",
    "C:\\Windows\\System32",
    # Parent traversal
    "..",
    "../etc/passwd",
    "content/../../../etc/passwd",
    "content/./../etc",
    "a/b/../../c",
    # Null byte injection
    "content/a\x00.py",
    "\x00",
    "content/x\x00/../../etc",
    # Other control characters
    "content/x\n.py",
    "content/x\r.py",
    "content/\x01file",
    "content/file\x7f",
    # Unicode lookalikes that must NOT pass the ASCII-only regex
    "contént/main.py",
    "‮content.py",          # right-to-left override
    "﻿file.py",             # BOM
    " space.py",            # NBSP
    "․dot.py",              # one-dot leader
    "file­.py",             # soft hyphen
    "​zero.py",             # zero-width space
    # Encoded traversal attempts (must be rejected literally, no decoding)
    "content/%2e%2e/etc",
    "content/%2E%2E/etc",
    "content/%2f%2fetc",
    "content/%5c%5cwin",
    "content/%00.py",
    # Spaces and special shell characters
    "content/file with spaces.py",
    "content/$(id).py",
    "content/`id`.py",
    "content/file;rm.py",
    "content/file|cat.py",
    "content/file&cat.py",
    "content/file?.py",
    "content/file*.py",
    "content/file[.py",
    "content/file].py",
    "content/file{.py",
    "content/file}.py",
    "content/file<.py",
    "content/file>.py",
    "content/file\".py",
    "content/file'.py",
    "content/file\\.py",       # backslash mid-path (not normalized by _safe_relpath)
    # Windows drive / device names embedded
    "content/aux",
    "content/con",
    "content/CON",
    # Trailing slashes that imply directory traversal once normalized
    "content/",
    "/",
    "./content",
    # Symbolic prefixes
    "~/secret",
    "~root/secret",
    # Very long inputs (cover the regex tail behavior)
    "a/" * 600,
    "x" * 4096,
    # Bytes mistakenly decoded as latin-1 — must still be rejected
    "café.py",
]


def _make_pfp_safe_pkg(tmp_path: Path):
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    return pfp_package._load_package(built["path"], require_verified=True)


# `_safe_relpath` enforces three rules: not empty after slash normalization,
# no `..` segment, and a strict ASCII character whitelist
# (`_SAFE_PATH_RE = ^[A-Za-z0-9._/@:+-]+$`). It DOES normalize backslashes
# to forward slashes and strips leading/trailing slashes before checking,
# so values like `/etc/passwd` or `\windows\system32` are not rejected by
# the absolute-prefix rule — they are silently converted to `etc/passwd`
# and `windows/system32` and then re-checked. A signed package can never
# carry such paths in its zip directory because the zip is enumerated
# verbatim, so a malicious entry name keeps its leading slash and trips
# `path.startswith("/")` once `replace` is gone. This test pins the things
# `_safe_relpath` is meant to actually filter: parent traversal, null bytes,
# control chars, non-ASCII, shell metachars, and url-encoded equivalents.
_SAFE_RELPATH_REJECTS = [
    candidate for candidate in _UNSAFE_PATH_INPUTS
    if (
        # parent traversal
        any(part == ".." for part in candidate.replace("\\", "/").split("/"))
        # non-ASCII / control characters
        or any(ord(c) > 126 or ord(c) < 32 or c == "\x7f" for c in candidate)
        # shell metachars / quotes / spaces
        or any(c in candidate for c in (" ", "$", "`", ";", "|", "&", "?", "*",
                                         "[", "]", "{", "}", "<", ">", '"', "'"))
        # url-encoded payloads (percent sign is not in the safe regex)
        or "%" in candidate
        # tilde-home prefix (literal `~` is not in the safe regex)
        or candidate.startswith("~")
        # empty / whitespace-only after strip
        or candidate.strip().strip("/") == ""
    )
]


def test_safe_relpath_rejects_unicode_traversal_nulls_and_doubleencoded():
    """_safe_relpath must reject every unsafe shape covered by its contract."""
    accepted = []
    for candidate in _SAFE_RELPATH_REJECTS:
        try:
            pfp_package._safe_relpath(candidate)
        except pfp_package.PfpError:
            continue
        accepted.append(candidate)
    assert accepted == [], f"_safe_relpath accepted unsafe paths: {accepted!r}"


# The runtime path helpers (`_safe_entrypoint`, `_safe_artifact_relpath`)
# have a narrower contract than `_safe_relpath`:
#   - reject empty
#   - reject any segment equal to `..`
#   - reject absolute POSIX paths after slash normalization
# They intentionally do NOT enforce the ASCII-only character set, and they
# normalize backslashes to forward slashes before checking, so values such as
# "/etc/passwd", "\\windows\\system32" or url-encoded `%2e%2e` are accepted
# at this layer. The actual escape guard for those is the downstream
# `Path.resolve()` + `relative_to(content_dir)` containment check enforced
# by `prepare_runtime_entrypoint` and by `PackageRuntimeService` artifact
# normalization. The fuzz test below pins the narrow structural contract:
# every path containing a `..` segment must be rejected at the helper level.
_PARENT_TRAVERSAL_INPUTS = [
    candidate for candidate in _UNSAFE_PATH_INPUTS
    if any(part == ".." for part in candidate.replace("\\", "/").split("/"))
]
_EMPTY_OR_WHITESPACE_INPUTS = ["", "/", "//", "\\", "\\\\"]


def test_safe_entrypoint_rejects_parent_traversal_and_empty():
    from core import pfp_runtime
    cases = _PARENT_TRAVERSAL_INPUTS + _EMPTY_OR_WHITESPACE_INPUTS
    accepted = []
    for candidate in cases:
        try:
            pfp_runtime._safe_entrypoint(candidate)
        except pfp_runtime.PackageRuntimeError:
            continue
        accepted.append(candidate)
    assert accepted == [], (
        f"_safe_entrypoint accepted parent-traversal/empty inputs: {accepted!r}")


def test_safe_artifact_relpath_rejects_parent_traversal_and_empty():
    from core import pfp_runtime
    cases = _PARENT_TRAVERSAL_INPUTS + _EMPTY_OR_WHITESPACE_INPUTS
    accepted = []
    for candidate in cases:
        try:
            pfp_runtime._safe_artifact_relpath(candidate)
        except pfp_runtime.PackageRuntimeError:
            continue
        accepted.append(candidate)
    assert accepted == [], (
        f"_safe_artifact_relpath accepted parent-traversal/empty inputs: {accepted!r}")


def test_safe_relpath_is_stricter_than_runtime_helpers():
    """Document the intentional contract gap between build-time and runtime checks."""
    from core import pfp_runtime
    # Each value: rejected by `_safe_relpath` (ASCII-only build-time check),
    # accepted by the runtime helpers (their narrower structural contract
    # delegates character validation to the resolve+relative_to containment
    # downstream). These values must never end up in a signed package
    # because `_safe_relpath` runs on every file at build time.
    runtime_accepted_but_build_rejected = []
    for candidate in [
        "content/file with spaces.py",   # space
        "café.py",                       # non-ASCII
        "content/%2e%2e/etc",             # url-encoded traversal kept literal
        "content/$(id).py",               # shell metachars
        "content/x\x00.py",               # null byte
    ]:
        try:
            pfp_package._safe_relpath(candidate)
        except pfp_package.PfpError:
            pass
        else:
            raise AssertionError(
                f"_safe_relpath was expected to reject {candidate!r}")
        try:
            pfp_runtime._safe_entrypoint(candidate)
            runtime_accepted_but_build_rejected.append(candidate)
        except pfp_runtime.PackageRuntimeError:
            pass
    assert runtime_accepted_but_build_rejected, (
        "Runtime helpers are now as strict as the build-time check; either"
        " tighten this test or move the values into _PARENT_TRAVERSAL_INPUTS."
    )


def test_pfp_zip_with_unsafe_entry_path_is_rejected(tmp_path):
    """Build a hand-crafted .pfp whose lock advertises a traversal path; load_package must reject."""
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    poisoned = tmp_path / "poisoned.pfp"
    # Replicate the safe archive verbatim, then add an extra entry with
    # a traversal name that escapes the package root.
    with zipfile.ZipFile(built["path"], "r") as src, zipfile.ZipFile(poisoned, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("../escape.txt", b"pwn")

    try:
        pfp_package._load_package(str(poisoned), require_verified=True)
    except pfp_package.PfpError:
        pass
    else:
        raise AssertionError("package with traversal entry path must be rejected")


def test_pfp_zip_with_null_byte_entry_path_is_rejected(tmp_path):
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(
        str(pkgdir), private_key=keypair["private_key"])
    poisoned = tmp_path / "poisoned.pfp"
    with zipfile.ZipFile(built["path"], "r") as src, zipfile.ZipFile(poisoned, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("content/inject\x00.py", b"pwn")

    try:
        pfp_package._load_package(str(poisoned), require_verified=True)
    except pfp_package.PfpError:
        pass
    else:
        raise AssertionError("package with null-byte entry path must be rejected")


def test_pfp_build_accepts_private_key_env(tmp_path, monkeypatch):
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    monkeypatch.setenv("TEST_PFP_SIGNING_KEY", keypair["private_key"])

    built = pfp_package.build_pfp(
        str(pkgdir), private_key_env="TEST_PFP_SIGNING_KEY")

    assert built["ok"] is True
    assert pfp_package.inspect_pfp(built["path"])["verified"] is True


def test_pfp_registry_search_and_install_ref(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_bytes = (pkgdir / "dist" / "community.wavespeed-1.0.0.pfp").read_bytes()
    index = {
        "format": "pawflow.package.registry.v1",
        "registry": "test-registry",
        "packages": [{
            "package": "community.wavespeed",
            "version": "1.0.0",
            "description": "WaveSpeed media provider",
            "pfp_url": "https://registry.example/community.wavespeed-1.0.0.pfp",
            "sha256": built["sha256"],
            "package_size": built["package_size"],
            "tags": ["media", "image"],
            "objects": ["skill:pkg-skill"],
        }],
    }

    def fake_get(url, headers=None, **kwargs):
        if url == "https://registry.example/index.json":
            return _Response(json.dumps(index).encode("utf-8"))
        if url == "https://registry.example/community.wavespeed-1.0.0.pfp":
            return _Response(pfp_bytes)
        return _Response(b"not found", status_code=404)

    monkeypatch.setattr(pfp_registry.requests, "get", fake_get)

    added = pfp_registry.add_registry(
        "https://registry.example/index.json", user_id="alice", trusted=True)
    assert added["registry"]["name"] == "test-registry"
    assert added["registry"]["trusted"] is True

    search = pfp_registry.search_registries("wavespeed image", user_id="alice")
    assert search["count"] == 1
    assert search["results"][0]["ref"] == "community.wavespeed@1.0.0"
    assert search["results"][0]["registry_trusted"] is True
    assert search["results"][0]["package_size"] == built["package_size"]

    preflight = pfp_registry.resolve_package_path(
        "community.wavespeed@1.0.0", user_id="alice")
    assert preflight["requires_confirmation"] is True
    assert preflight["package_size"] == built["package_size"]
    resolved = pfp_registry.resolve_package_path(
        "community.wavespeed@1.0.0", user_id="alice", confirm_download=True)
    assert resolved["downloaded"] is True
    assert resolved["sha256"] == built["sha256"]

    installed = pfp_package.install_pfp(
        resolved["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert installed["ok"] is True


def test_pfp_direct_url_requires_size_confirmation_before_download(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_bytes = Path(built["path"]).read_bytes()
    url = "https://registry.example/community.wavespeed-1.0.0.pfp"
    calls = {"head": 0, "get": 0}

    def fake_head(head_url, headers=None, **kwargs):
        assert head_url == url
        calls["head"] += 1
        return _Response(b"", headers={"Content-Length": str(len(pfp_bytes))})

    def fake_get(get_url, headers=None, **kwargs):
        assert get_url == url
        calls["get"] += 1
        return _Response(pfp_bytes)

    monkeypatch.setattr(pfp_registry.requests, "head", fake_head)
    monkeypatch.setattr(pfp_registry.requests, "get", fake_get)

    preflight = pfp_registry.resolve_package_path(url, user_id="alice")

    assert preflight["requires_confirmation"] is True
    assert preflight["package_size"] == len(pfp_bytes)
    assert calls == {"head": 1, "get": 0}

    resolved = pfp_registry.resolve_package_path(
        url, user_id="alice", expected_sha256=built["sha256"], confirm_download=True)

    assert resolved["downloaded"] is True
    assert resolved["package_size"] == len(pfp_bytes)
    assert calls == {"head": 2, "get": 1}


def test_pfp_registry_update_cycle_handles_add_update_remove_and_uninstall(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir_v1 = _write_package_dir(tmp_path / "v1", keypair)
    built_v1 = pfp_package.build_pfp(str(pkgdir_v1), private_key=keypair["private_key"])

    pkgdir_v2 = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", skill_body="Updated registry skill.")
    prompt_dir = pkgdir_v2 / "content" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "package-prompt.json").write_text(json.dumps({
        "prompt": "Prompt added in v2",
        "description": "Registry update prompt",
    }), encoding="utf-8")
    manifest_path = pkgdir_v2 / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["objects"] = [
        obj for obj in manifest["objects"]
        if obj["id"] != "agent:helper"
    ]
    manifest["objects"].append({
        "id": "prompt:package-prompt",
        "type": "prompt",
        "name": "package-prompt",
        "path": "content/prompts/package-prompt.json",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    built_v2 = pfp_package.build_pfp(str(pkgdir_v2), private_key=keypair["private_key"])

    pfp_bytes = {
        "https://registry.example/community.wavespeed-1.0.0.pfp": Path(built_v1["path"]).read_bytes(),
        "https://registry.example/community.wavespeed-1.1.0.pfp": Path(built_v2["path"]).read_bytes(),
    }
    index = {
        "format": "pawflow.package.registry.v1",
        "registry": "test-registry",
        "packages": [
            {
                "package": "community.wavespeed",
                "version": "1.0.0",
                "description": "WaveSpeed media provider v1",
                "pfp_url": "https://registry.example/community.wavespeed-1.0.0.pfp",
                "sha256": built_v1["sha256"],
                "package_size": built_v1["package_size"],
                "objects": ["skill:pkg-skill", "agent:helper"],
            },
            {
                "package": "community.wavespeed",
                "version": "1.1.0",
                "description": "WaveSpeed media provider v2",
                "pfp_url": "https://registry.example/community.wavespeed-1.1.0.pfp",
                "sha256": built_v2["sha256"],
                "package_size": built_v2["package_size"],
                "objects": ["skill:pkg-skill", "prompt:package-prompt", "tool:reader"],
            },
        ],
    }

    def fake_get(url, headers=None, **kwargs):
        if url == "https://registry.example/index.json":
            return _Response(json.dumps(index).encode("utf-8"))
        if url in pfp_bytes:
            return _Response(pfp_bytes[url])
        return _Response(b"not found", status_code=404)

    monkeypatch.setattr(pfp_registry.requests, "get", fake_get)
    pfp_registry.add_registry("https://registry.example/index.json", user_id="alice")

    installed_path = pfp_registry.resolve_package_path(
        "community.wavespeed@1.0.0", user_id="alice", confirm_download=True)["path"]
    installed = pfp_package.install_pfp(
        installed_path, user_id="alice", include=["skill:pkg-skill", "agent:helper"], force=True)
    assert installed["ok"] is True

    search = pfp_registry.search_registries("provider v2", user_id="alice")
    assert search["results"][0]["ref"] == "community.wavespeed@1.1.0"
    update_path = pfp_registry.resolve_package_path(
        "community.wavespeed@1.1.0", user_id="alice", confirm_download=True)["path"]
    plan = pfp_package.inspect_pfp(update_path, user_id="alice")
    changes = {item["id"]: item["change"] for item in plan["update_diff"]["objects"]}
    assert changes["skill:pkg-skill"] == "update"
    assert changes["agent:helper"] == "remove"
    assert changes["prompt:package-prompt"] == "add"
    assert changes["tool:reader"] == "add"

    updated = pfp_package.update_pfp(update_path, user_id="alice", force=True)
    assert updated["ok"] is True
    assert [row["id"] for row in updated["updated"]] == ["skill:pkg-skill"]
    assert [row["id"] for row in updated["removed"]] == ["agent:helper"]

    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    assert store.get("skill", "pkg-skill", "alice")["instructions"] == "Updated registry skill."
    assert store.get("agent", "helper", "alice") is None
    assert store.get("prompt", "package-prompt", "alice") is None

    partial = pfp_package.update_pfp(
        update_path, user_id="alice", include=["prompt:package-prompt"], force=True)
    assert partial["ok"] is True
    assert [row["id"] for row in partial["updated"]] == ["prompt:package-prompt"]
    assert store.get("prompt", "package-prompt", "alice")["prompt"] == "Prompt added in v2"

    listed = pfp_package.list_installed_packages(user_id="alice")
    record = listed["packages"][0]
    assert record["version"] == "1.1.0"
    assert {obj["object_id"] for obj in record["objects"]} == {
        "skill:pkg-skill", "prompt:package-prompt"
    }

    removed = pfp_package.uninstall_pfp("community.wavespeed", user_id="alice")
    assert removed["ok"] is True
    assert pfp_package.list_installed_packages(user_id="alice")["packages"] == []


def test_pfp_action_layer_registry_install_update_uninstall_cycle(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir_v1 = _write_package_dir(tmp_path / "v1", keypair)
    built_v1 = pfp_package.build_pfp(str(pkgdir_v1), private_key=keypair["private_key"])
    pkgdir_v2 = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", skill_body="Action layer update.")
    built_v2 = pfp_package.build_pfp(str(pkgdir_v2), private_key=keypair["private_key"])
    pfp_bytes = {
        "https://registry.example/community.wavespeed-1.0.0.pfp": Path(built_v1["path"]).read_bytes(),
        "https://registry.example/community.wavespeed-1.1.0.pfp": Path(built_v2["path"]).read_bytes(),
    }
    index = {
        "format": "pawflow.package.registry.v1",
        "registry": "test-registry",
        "packages": [
            {
                "package": "community.wavespeed",
                "version": "1.0.0",
                "pfp_url": "https://registry.example/community.wavespeed-1.0.0.pfp",
                "sha256": built_v1["sha256"],
                "package_size": built_v1["package_size"],
            },
            {
                "package": "community.wavespeed",
                "version": "1.1.0",
                "pfp_url": "https://registry.example/community.wavespeed-1.1.0.pfp",
                "sha256": built_v2["sha256"],
                "package_size": built_v2["package_size"],
            },
        ],
    }

    def fake_get(url, headers=None, **kwargs):
        if url == "https://registry.example/index.json":
            return _Response(json.dumps(index).encode("utf-8"))
        if url in pfp_bytes:
            return _Response(pfp_bytes[url])
        return _Response(b"not found", status_code=404)

    monkeypatch.setattr(pfp_registry.requests, "get", fake_get)

    from core import FlowFile
    from tasks.ai.actions.agent_resource import _handle_agent_resource

    def call(action, body):
        ff = FlowFile(content=b"")
        ff.set_attribute("http.auth.roles", "user")
        handled = _handle_agent_resource(None, action, body, object(), "alice", ff)
        assert handled == [ff]
        return json.loads(ff.get_content().decode("utf-8"))

    added = call("pfp_registry_add", {
        "url": "https://registry.example/index.json", "trusted": True})
    assert added["registry"]["name"] == "test-registry"
    assert added["registry"]["trusted"] is True
    search = call("pfp_search", {"query": "wavespeed", "limit": 5})
    assert {row["ref"] for row in search["results"]} == {
        "community.wavespeed@1.0.0", "community.wavespeed@1.1.0"
    }

    inspected = call("pfp_inspect", {"path": "community.wavespeed@1.0.0"})
    assert inspected["requires_confirmation"] is True
    assert inspected["package_size"] == built_v1["package_size"]

    inspected = call("pfp_inspect", {
        "path": "community.wavespeed@1.0.0",
        "confirm_download": True,
    })
    assert inspected["verified"] is True
    assert inspected["download"]["sha256"] == built_v1["sha256"]

    install_preflight = call("pfp_install", {
        "path": "community.wavespeed@1.0.0",
        "include": ["skill:pkg-skill"],
        "force": True,
    })
    assert install_preflight["requires_confirmation"] is True
    assert install_preflight["package_size"] == built_v1["package_size"]

    installed = call("pfp_install", {
        "path": "community.wavespeed@1.0.0",
        "include": ["skill:pkg-skill"],
        "force": True,
        "confirm_download": True,
    })
    assert installed["ok"] is True

    update_preflight = call("pfp_update", {
        "path": "community.wavespeed@1.1.0",
        "force": True,
    })
    assert update_preflight["requires_confirmation"] is True
    assert update_preflight["package_size"] == built_v2["package_size"]

    updated = call("pfp_update", {
        "path": "community.wavespeed@1.1.0",
        "force": True,
        "confirm_download": True,
    })
    assert updated["ok"] is True
    assert updated["updated"][0]["id"] == "skill:pkg-skill"

    listed = call("pfp_list_installed", {})
    assert listed["packages"][0]["version"] == "1.1.0"

    removed = call("pfp_uninstall", {"package": "community.wavespeed"})
    assert removed["ok"] is True


def test_pfp_update_replaces_installed_object_and_record(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built_v1 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    installed = pfp_package.install_pfp(
        built_v1["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert installed["ok"] is True

    pkgdir = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", skill_body="Updated skill body.")
    built_v2 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    updated = pfp_package.update_pfp(built_v2["path"], user_id="alice", force=True)

    assert updated["ok"] is True
    assert updated["updated"][0]["id"] == "skill:pkg-skill"
    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("skill", "pkg-skill", "alice")
    assert stored["prompt"] == "Updated skill body."

    listed = pfp_package.list_installed_packages(user_id="alice")
    record = listed["packages"][0]
    assert record["version"] == "1.1.0"
    assert len(record["objects"]) == 1
    assert record["objects"][0]["object_id"] == "skill:pkg-skill"


def test_pfp_inspect_reports_update_diff_for_installed_package(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built_v1 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built_v1["path"], user_id="alice", include=["skill:pkg-skill"], force=True)

    pkgdir = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", skill_body="Updated skill body.")
    built_v2 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    plan = pfp_package.inspect_pfp(built_v2["path"], user_id="alice")

    assert plan["update_diff"]["installed"] is True
    assert plan["update_diff"]["from_version"] == "1.0.0"
    assert plan["update_diff"]["to_version"] == "1.1.0"
    assert plan["update_diff"]["version_change"] == "upgrade"
    skill_row = next(row for row in plan["objects"] if row["id"] == "skill:pkg-skill")
    assert skill_row["update_diff"]["change"] == "update"
    assert skill_row["update_diff"]["from_hash"] != skill_row["update_diff"]["to_hash"]

    display = pfp_package.format_inspection_display(plan)
    assert "Update: 1.0.0 -> 1.1.0 (upgrade); update:skill:pkg-skill" in display


def test_pfp_update_blocks_installed_dependents_with_version_constraints(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    base_dir = _write_package_dir(
        tmp_path / "base", keypair,
        package_id="community.base", skill_name="base-skill")
    base_v1 = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base_v1["path"], user_id="alice", include=["tool:reader"], force=True)

    dependent_dir = _write_package_dir(
        tmp_path / "dependent", keypair,
        package_id="community.consumer", include_service_provider=True)
    manifest_path = dependent_dir / "pfp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provider = next(obj for obj in manifest["objects"] if obj["id"] == "service_provider:image")
    provider["allowed_tools"] = [{
        "package": "community.base",
        "version": "^1.0.0",
        "object": "tool:reader",
    }]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dependent = pfp_package.build_pfp(str(dependent_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        dependent["path"], user_id="alice", include=["service_provider:image"], force=True)

    base_v1_5_dir = _write_package_dir(
        tmp_path / "base-v1-5", keypair, version="1.5.0",
        package_id="community.base", skill_name="base-skill")
    base_v1_5 = pfp_package.build_pfp(str(base_v1_5_dir), private_key=keypair["private_key"])
    compatible = pfp_package.update_pfp(base_v1_5["path"], user_id="alice")
    assert compatible["ok"] is True
    assert compatible["updated"][0]["id"] == "tool:reader"

    base_v2_dir = _write_package_dir(
        tmp_path / "base-v2", keypair, version="2.0.0",
        package_id="community.base", skill_name="base-skill")
    base_v2 = pfp_package.build_pfp(str(base_v2_dir), private_key=keypair["private_key"])
    blocked = pfp_package.update_pfp(base_v2["path"], user_id="alice")

    assert blocked["ok"] is False
    assert blocked["reason"] == "dependent_version_conflict"
    assert blocked["updated"] == []
    assert blocked["blocked_by"] == [{
        "package": "community.consumer",
        "version": "1.0.0",
        "required_version": "^1.0.0",
        "object": "tool:reader",
    }]

    forced = pfp_package.update_pfp(base_v2["path"], user_id="alice", force=True)
    assert forced["ok"] is True
    assert forced["updated"][0]["id"] == "tool:reader"


def test_pfp_update_preserves_existing_secret_bindings(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, tool_runner="python",
        tool_secrets=[{"name": "api_key", "env": "PROVIDER_API_KEY"}])
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': 'v1:' + os.environ['PROVIDER_API_KEY'],\n"
        "}))\n",
        encoding="utf-8",
    )
    built_v1 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    from core.paths import user_secrets_path
    ConfigStore.save_secrets(user_secrets_path("alice"), {
        "provider_key": ConfigValue(value="sk-v1"),
    })
    pfp_package.install_pfp(
        built_v1["path"], user_id="alice", include=["tool:reader"], force=True,
        secret_bindings={"api_key": "provider_key"})

    pkgdir = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", tool_runner="python",
        tool_secrets=[{"name": "api_key", "env": "PROVIDER_API_KEY"}])
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': 'v2:' + os.environ['PROVIDER_API_KEY'],\n"
        "}))\n",
        encoding="utf-8",
    )
    built_v2 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    updated = pfp_package.update_pfp(built_v2["path"], user_id="alice", force=True)
    assert updated["ok"] is True

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    assert stored["package_runtime"]["secret_bindings"] == {"api_key": "provider_key"}




def test_pfp_update_allows_secret_binding_override(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, tool_runner="python",
        tool_secrets=[{"name": "api_key", "env": "PROVIDER_API_KEY"}])
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, os\n"
        "print(json.dumps({'format': 'pawflow.package.runtime.result.v1', 'ok': True, 'result': os.environ['PROVIDER_API_KEY']}))\n",
        encoding="utf-8",
    )
    built_v1 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    from core.paths import user_secrets_path
    ConfigStore.save_secrets(user_secrets_path("alice"), {
        "old_key": ConfigValue(value="old-secret"),
        "new_key": ConfigValue(value="new-secret"),
    })
    pfp_package.install_pfp(
        built_v1["path"], user_id="alice", include=["tool:reader"], force=True,
        secret_bindings={"api_key": "old_key"})

    pkgdir = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", tool_runner="python",
        tool_secrets=[{"name": "api_key", "env": "PROVIDER_API_KEY"}])
    (pkgdir / "content" / "tools" / "reader" / "main.py").write_text(
        "import json, os\n"
        "print(json.dumps({'format': 'pawflow.package.runtime.result.v1', 'ok': True, 'result': os.environ['PROVIDER_API_KEY']}))\n",
        encoding="utf-8",
    )
    built_v2 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    updated = pfp_package.update_pfp(
        built_v2["path"], user_id="alice", force=True,
        secret_bindings={"api_key": "new_key"})
    assert updated["ok"] is True

    from core.resource_store import ResourceStore
    stored = ResourceStore.instance().get("tool", "reader", "alice")
    assert stored["package_runtime"]["secret_bindings"] == {"api_key": "new_key"}


def test_pfp_update_skips_local_modification_without_force(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built_v1 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built_v1["path"], user_id="alice", include=["skill:pkg-skill"], force=True)

    from core.resource_store import ResourceStore
    ResourceStore.instance().update("skill", "pkg-skill", "alice", {
        "installed_from": {"hash": "sha256:local-change"},
    })

    pkgdir = _write_package_dir(
        tmp_path / "v2", keypair, version="1.1.0", skill_body="Updated skill body.")
    built_v2 = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    updated = pfp_package.update_pfp(built_v2["path"], user_id="alice")

    assert updated["ok"] is False
    assert updated["updated"] == []
    assert updated["skipped"] == [{"id": "skill:pkg-skill", "reason": "local_modified"}]


def test_pfp_export_creates_source_package(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.resource_store import ResourceStore

    ResourceStore.instance().create("skill", "local-skill", "alice", {
        "description": "Local",
        "instructions": "Local skill prompt",
    })
    skill_root = Path(ResourceStore.instance().get(
        "skill", "local-skill", "alice")["skill_root"])
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "scripts" / "run.sh").write_text(
        "echo local\n", encoding="utf-8")
    # A binary asset must survive export byte-for-byte — nothing dropped.
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff\xfe"
    (skill_root / "assets").mkdir(parents=True)
    (skill_root / "assets" / "logo.png").write_bytes(png_bytes)

    exported = pfp_package.export_pfpdir(
        "alice.local", "0.1.0", ["skill:local-skill"],
        output_dir=str(tmp_path / "exported.pfpdir"), user_id="alice")

    assert exported["ok"] is True
    manifest = json.loads((tmp_path / "exported.pfpdir" / "pfp.json").read_text(encoding="utf-8"))
    assert manifest["package"] == "alice.local"
    assert manifest["objects"][0]["id"] == "skill:local-skill"
    assert manifest["objects"][0]["path"] == "content/skills/local-skill/SKILL.md"
    skill_md = tmp_path / "exported.pfpdir" / "content" / "skills" / "local-skill" / "SKILL.md"
    assert "name: local-skill" in skill_md.read_text(encoding="utf-8")
    assert "Local skill prompt" in skill_md.read_text(encoding="utf-8")
    exported_script = (
        tmp_path / "exported.pfpdir" / "content" / "skills"
        / "local-skill" / "scripts" / "run.sh"
    )
    assert exported_script.read_text(encoding="utf-8") == "echo local\n"
    exported_png = (
        tmp_path / "exported.pfpdir" / "content" / "skills"
        / "local-skill" / "assets" / "logo.png"
    )
    assert exported_png.read_bytes() == png_bytes
    # Review-pipeline metadata must not leak into the portable SKILL.md.
    assert "review:" not in skill_md.read_text(encoding="utf-8")


def test_pfp_export_agent_includes_assigned_skills(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core.resource_store import ResourceStore

    store = ResourceStore.instance()
    store.create("skill", "review-pr", "alice", {
        "description": "Review PRs",
        "instructions": "Review the PR.",
    })
    store.create("agent", "assistant", "alice", {
        "prompt": "You are an assistant.",
        "description": "Assistant",
        "assigned_skills": [{"name": "review-pr", "params": {"mode": "fast"}}],
    })

    exported = pfp_package.export_pfpdir(
        "alice.agent", "0.1.0", ["agent:assistant"],
        output_dir=str(tmp_path / "agent-export.pfpdir"), user_id="alice")

    assert exported["ok"] is True
    assert [obj["id"] for obj in exported["objects"]] == [
        "agent:assistant", "skill:review-pr"]
    skill_md = (
        tmp_path / "agent-export.pfpdir" / "content" / "skills"
        / "review-pr" / "SKILL.md"
    )
    assert "Review the PR." in skill_md.read_text(encoding="utf-8")


def test_pfp_install_skips_agent_when_assigned_skill_not_selected(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    agent_path = pkgdir / "content" / "agents" / "helper.json"
    agent_data = json.loads(agent_path.read_text(encoding="utf-8"))
    agent_data["assigned_skills"] = ["pkg-skill"]
    agent_path.write_text(json.dumps(agent_data), encoding="utf-8")
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    result = pfp_package.install_pfp(
        built["path"], user_id="alice", include=["agent:helper"])

    skipped = {row["id"]: row for row in result["skipped"]}
    assert skipped["agent:helper"]["reason"] == "missing_dependency"
    assert skipped["agent:helper"]["missing_assigned_skills"] == ["pkg-skill"]


def test_pfp_uninstall_skill_cleans_agent_assignments(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair)
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["skill:pkg-skill"])

    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    store.create("agent", "assistant", "alice", {
        "prompt": "You are an assistant.",
        "assigned_skills": ["pkg-skill", "other"],
    })

    removed = pfp_package.uninstall_pfp(
        "community.wavespeed", user_id="alice", force=True)

    assert removed["ok"] is True
    agent = store.get("agent", "assistant", "alice")
    assert agent["assigned_skills"] == ["other"]


def test_pfp_slash_parser_handles_install_flags():
    body = _parse_command(
        "/pfp install ./dist/pkg.pfp --scope conversation --include skill:a,flow:b --exclude tool:c --force --replace",
        "conv1", "alice", "assistant")

    assert body["action"] == "pfp_install"
    assert body["path"] == "./dist/pkg.pfp"
    assert body["scope"] == "conversation"
    assert body["include"] == ["skill:a", "flow:b"]
    assert body["exclude"] == ["tool:c"]
    assert body["force"] is True
    assert body["replace"] is True


def test_pfp_slash_parser_handles_update():
    body = _parse_command(
        "/pfp update community.wavespeed@1.1.0 --include skill:pkg-skill --force",
        "conv1", "alice", "assistant")

    assert body["action"] == "pfp_update"
    assert body["path"] == "community.wavespeed@1.1.0"
    assert body["include"] == ["skill:pkg-skill"]
    assert body["force"] is True


def test_pfp_slash_parser_handles_reload_tasks():
    body = _parse_command(
        "/pfp reload-tasks --scope conversation",
        "conv1", "alice", "assistant")

    assert body["action"] == "pfp_reload_tasks"
    assert body["scope"] == "conversation"


def test_pfp_slash_parser_handles_registry_and_search():
    add = _parse_command(
        "/pfp registry add https://registry.example/index.json --name community --trusted",
        "conv1", "alice", "assistant")
    assert add["action"] == "pfp_registry_add"
    assert add["url"] == "https://registry.example/index.json"
    assert add["name"] == "community"
    assert add["trusted"] is True

    search = _parse_command(
        "/pfp search wavespeed image --limit 5",
        "conv1", "alice", "assistant")
    assert search["action"] == "pfp_search"
    assert search["query"] == "wavespeed image"
    assert search["limit"] == 5


def test_manage_package_tool_is_registered():
    from core.tool_registry import create_default_registry

    registry = create_default_registry()
    assert registry.get("manage_package") is not None


def test_skill_bundled_files_reconstructed_on_install():
    # P1: a PFP skill object must carry its sibling assets through install,
    # not just SKILL.md, so ${CLAUDE_SKILL_DIR}/scripts/... resolves.
    package = {
        "files": {
            "content/skills/demo/SKILL.md":
                b"---\nname: demo\ndescription: d\n---\nBody.",
            "content/skills/demo/scripts/go.sh": b"echo hi\n",
            "content/skills/demo/references/api.md": b"# API\n",
            "content/skills/other/SKILL.md":
                b"---\nname: other\ndescription: d\n---\nX.",
        },
    }
    rel = "content/skills/demo/SKILL.md"
    bundled = pfp_package._skill_bundled_files(package, rel)
    assert set(bundled) == {"scripts/go.sh", "references/api.md"}
    # Assets travel verbatim as bytes so binary files survive the round-trip.
    assert bundled["scripts/go.sh"] == b"echo hi\n"
    # Sibling skill 'other' must not leak in.
    data = pfp_package._load_resource_data(package, rel, "skill", "demo")
    assert data["package_files"]["scripts/go.sh"] == b"echo hi\n"
    assert "references/api.md" in data["package_files"]


def test_skill_install_writes_binary_assets_verbatim(tmp_path, monkeypatch):
    # A binary asset bundled with an installed skill must land on disk
    # byte-for-byte — nothing dropped or lossily decoded.
    _reset_repo(tmp_path, monkeypatch)
    from core.resource_store import ResourceStore

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff\xfe"
    ResourceStore.instance().create("skill", "bin-skill", "alice", {
        "description": "Bin",
        "instructions": "Body.",
        "package_files": {"assets/logo.png": png_bytes},
    })
    skill_root = Path(ResourceStore.instance().get(
        "skill", "bin-skill", "alice")["skill_root"])
    assert (skill_root / "assets" / "logo.png").read_bytes() == png_bytes

