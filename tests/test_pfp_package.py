import json
import zipfile
from pathlib import Path

from core import pfp_package
from core import pfp_registry
from tasks.ai.actions.command_dispatch import _parse_command


class _Response:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


def _write_package_dir(root, keypair, version="1.0.0", skill_body="Use the package skill safely.",
                       package_id="community.wavespeed", skill_name="pkg-skill",
                       dependencies=None, tool_allowed_tools=None,
                       include_service_provider=False, include_flow_task=False,
                       tool_runner="", service_runner="", flow_task_runner="",
                       tool_secrets=None):
    pkg = root / "wavespeed-provider.pfpdir"
    skill_dir = pkg / "content" / "skills" / skill_name
    agent_dir = pkg / "content" / "agents"
    tool_dir = pkg / "content" / "tools" / "reader"
    service_provider_dir = pkg / "content" / "service-providers" / "image"
    flow_task_dir = pkg / "content" / "flow-tasks" / "image-resize"
    skill_dir.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    if include_service_provider:
        service_provider_dir.mkdir(parents=True)
    if include_flow_task:
        flow_task_dir.mkdir(parents=True)
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
    if dependencies:
        manifest["dependencies"] = dependencies
    (pkg / "pfp.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pkg


def _reset_repo(tmp_path, monkeypatch):
    import core.paths as paths
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
    assert "allowed tools: read, community.base/tool:normalize" in display
    assert "dependencies: community.base@1.0.0" in display
    assert "secrets: api_key->PROVIDER_API_KEY" in display


def test_pfp_installs_tool_as_non_executing_proxy(tmp_path, monkeypatch):
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
    assert "PFP runtime bridge is not implemented yet" in handler.execute({})

    removed = pfp_package.uninstall_pfp("community.wavespeed", user_id="alice", force=True)
    assert removed["ok"] is True
    assert not content_dir.exists()


def test_pfp_tool_proxy_executes_declared_python_subprocess_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, tool_runner="python_subprocess")
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "name = request['payload']['arguments']['name']\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': 'hello ' + name,\n"
        "}))\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()
    assert load_tools_into_registry(registry, "alice") == 1
    handler = registry.get("reader")
    handler.set_user_id("alice")

    assert handler.execute({"name": "Ada"}) == "hello Ada"


def test_pfp_tool_proxy_executes_declared_python_subprocess_host_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(tmp_path, keypair, tool_runner="python_subprocess_host")
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.host_call.v1',\n"
        "    'kind': 'tool',\n"
        "    'target': 'read',\n"
        "    'arguments': {'path': request['payload']['arguments']['path']},\n"
        "}), flush=True)\n"
        "response = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': response['ok'],\n"
        "    'result': response['result'],\n"
        "}), flush=True)\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["tool:reader"], force=True)

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry

    class _ReadTool:
        @property
        def name(self):
            return "read"

        def execute(self, arguments):
            assert arguments == {"path": "input.txt"}
            return "file-content"

    registry = ToolRegistry()
    registry.register(_ReadTool())
    monkeypatch.setattr(ToolRegistry, "_live_registry", registry)
    assert load_tools_into_registry(registry, "alice") == 1
    handler = registry.get("reader")
    handler.set_user_id("alice")

    assert handler.execute({"path": "input.txt"}) == "file-content"


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


def test_pfp_tool_runner_injects_bound_secret_env(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, tool_runner="python_subprocess",
        tool_secrets=[{"name": "api_key", "env": "WAVESPEED_API_KEY"}])
    entrypoint = pkgdir / "content" / "tools" / "reader" / "main.py"
    entrypoint.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': os.environ['WAVESPEED_API_KEY'],\n"
        "}))\n",
        encoding="utf-8",
    )
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
    assert stored["package_runtime"]["secret_bindings"] == {"api_key": "wavespeed_key"}
    assert stored["package_runtime"]["secrets"][0]["env"] == "WAVESPEED_API_KEY"

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry
    registry = ToolRegistry()
    load_tools_into_registry(registry, "alice")
    handler = registry.get("reader")
    handler.set_user_id("alice")

    assert handler.execute({}) == "sk-test"


def test_pfp_install_command_parses_secret_bindings():
    parsed = _parse_command(
        "/pfp install ./pkg.pfp --include tool:reader --secret api_key=wavespeed_key",
        "conv1", "alice", "assistant",
    )

    assert parsed["action"] == "pfp_install"
    assert parsed["secret_bindings"] == {"api_key": "wavespeed_key"}


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
    assert request["package"]["allowed_tools"] == [{"name": "read"}]
    assert request["package"]["allowed_services"] == []
    assert request["payload"] == {"arguments": {"path": "in.txt"}}


def test_pfp_runtime_bridge_receives_verified_tool_envelope(tmp_path, monkeypatch):
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

    class _Bridge(pfp_runtime.PackageRuntimeBridge):
        def invoke(self, request):
            calls.append(request)
            return "bridge-result"

    pfp_runtime.set_runtime_bridge(_Bridge())
    try:
        result = pfp_runtime.invoke_tool(
            stored["package_runtime"], stored["installed_from"], {"path": "in.txt"})
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert result == "bridge-result"
    assert len(calls) == 1
    assert calls[0]["format"] == "pawflow.package.runtime.invoke.v1"
    assert calls[0]["kind"] == "tool"
    assert calls[0]["package"]["object_id"] == "tool:reader"
    assert calls[0]["payload"] == {"arguments": {"path": "in.txt"}}


def test_pfp_python_subprocess_bridge_executes_entrypoint(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "name = request['payload']['arguments']['name']\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': True,\n"
        "    'result': 'hello ' + name,\n"
        "}))\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge())
    try:
        result = pfp_runtime.invoke_tool({
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:hello",
            "content_dir": str(content_dir),
            "entrypoint": "main.py",
            "runtime": "python",
        }, {"hash": digest}, {"name": "Ada"}, {"user_id": "alice"})
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert result == "hello Ada"


def test_pfp_python_subprocess_bridge_rejects_noisy_stdout(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "import json\n"
        "print('debug noise')\n"
        "print(json.dumps({'format': 'pawflow.package.runtime.result.v1', 'ok': True, 'result': 'ok'}))\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge())
    try:
        try:
            pfp_runtime.invoke_tool({
                "package": "community.runner",
                "version": "1.0.0",
                "object_id": "tool:noisy",
                "content_dir": str(content_dir),
                "entrypoint": "main.py",
                "runtime": "python",
            }, {"hash": digest}, {}, {"user_id": "alice"})
        except pfp_runtime.PackageRuntimeError as exc:
            assert "exactly one JSON result line" in str(exc)
        else:
            raise AssertionError("noisy subprocess stdout should be rejected")
    finally:
        pfp_runtime.set_runtime_bridge(None)


def test_pfp_python_subprocess_bridge_propagates_structured_error(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "import json\n"
        "print(json.dumps({'format': 'pawflow.package.runtime.result.v1', 'ok': False, 'error': 'bad input'}))\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge())
    try:
        try:
            pfp_runtime.invoke_tool({
                "package": "community.runner",
                "version": "1.0.0",
                "object_id": "tool:error",
                "content_dir": str(content_dir),
                "entrypoint": "main.py",
                "runtime": "python",
            }, {"hash": digest}, {}, {"user_id": "alice"})
        except pfp_runtime.PackageRuntimeError as exc:
            assert "bad input" in str(exc)
        else:
            raise AssertionError("runtime result ok=false should raise")
    finally:
        pfp_runtime.set_runtime_bridge(None)


def test_pfp_python_subprocess_bridge_handles_host_call_ipc(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.host_call.v1',\n"
        "    'kind': 'tool',\n"
        "    'target': 'read',\n"
        "    'arguments': {'path': request['payload']['arguments']['path']},\n"
        "}), flush=True)\n"
        "response = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({\n"
        "    'format': 'pawflow.package.runtime.result.v1',\n"
        "    'ok': response['ok'],\n"
        "    'result': 'host returned ' + response['result'],\n"
        "}), flush=True)\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime

    class _Tool:
        def execute(self, arguments):
            assert arguments == {"path": "input.txt"}
            return "file-content"

    class _Registry:
        def get(self, name):
            return _Tool() if name == "read" else None

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:hosted",
            "allowed_tools": [{"name": "read"}],
        },
        tool_registry=_Registry(),
    )
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge(host=host))
    try:
        result = pfp_runtime.invoke_tool({
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:hosted",
            "content_dir": str(content_dir),
            "entrypoint": "main.py",
            "runtime": "python",
        }, {"hash": digest}, {"path": "input.txt"}, {"user_id": "alice"})
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert result == "host returned file-content"


def test_pfp_python_subprocess_bridge_exposes_pfp_sdk(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "from pawflow import pfp\n"
        "name = pfp.payload['arguments']['name']\n"
        "pfp.result('hello ' + name)\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge())
    try:
        result = pfp_runtime.invoke_tool({
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:sdk",
            "content_dir": str(content_dir),
            "entrypoint": "main.py",
            "runtime": "python",
        }, {"hash": digest}, {"name": "Ada"}, {"user_id": "alice"})
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert result == "hello Ada"


def test_pfp_python_subprocess_bridge_sdk_calls_host_tool(tmp_path, monkeypatch):
    import hashlib

    _reset_repo(tmp_path, monkeypatch)
    content_dir = tmp_path / "runtime-content"
    content_dir.mkdir()
    entrypoint = content_dir / "main.py"
    entrypoint.write_text(
        "from pawflow import pfp\n"
        "result = pfp.call_tool('read', path=pfp.payload['arguments']['path'])\n"
        "pfp.result('host returned ' + result)\n",
        encoding="utf-8",
    )
    digest = "sha256:" + hashlib.sha256(entrypoint.read_bytes()).hexdigest()

    from core import pfp_runtime

    class _Tool:
        def execute(self, arguments):
            assert arguments == {"path": "input.txt"}
            return "file-content"

    class _Registry:
        def get(self, name):
            return _Tool() if name == "read" else None

    host = pfp_runtime.PackageRuntimeHost(
        user_id="alice",
        caller_runtime={
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:sdk-host",
            "allowed_tools": [{"name": "read"}],
        },
        tool_registry=_Registry(),
    )
    pfp_runtime.set_runtime_bridge(pfp_runtime.PythonSubprocessPackageRuntimeBridge(host=host))
    try:
        result = pfp_runtime.invoke_tool({
            "package": "community.runner",
            "version": "1.0.0",
            "object_id": "tool:sdk-host",
            "content_dir": str(content_dir),
            "entrypoint": "main.py",
            "runtime": "python",
        }, {"hash": digest}, {"path": "input.txt"}, {"user_id": "alice"})
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert result == "host returned file-content"


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

    class _Bridge(pfp_runtime.PackageRuntimeBridge):
        def invoke(self, request):
            calls.append(request)
            return "ok"

    registry = ToolRegistry()
    load_tools_into_registry(registry, "alice", conversation_id="conv1")
    handler = registry.get("reader")
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")

    pfp_runtime.set_runtime_bridge(_Bridge())
    try:
        assert handler.execute({"path": "in.txt"}) == "ok"
    finally:
        pfp_runtime.set_runtime_bridge(None)

    assert calls[0]["context"] == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "scope": "conversation",
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
    assert flowfile["content_b64"] == "aW1hZ2UtYnl0ZXM="


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
        "schema": {"width": {"type": "integer", "required": True}},
    }

    validation = _admin_validate_flow({"flow": {
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64}}},
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

    class _Bridge(pfp_runtime.PackageRuntimeBridge):
        def invoke(self, request):
            return {
                "format": "pawflow.package.runtime.result.v1",
                "ok": True,
                "flowfiles": [{
                    "content_b64": "b3V0",
                    "attributes": {"result": "ok"},
                }],
            }

    pfp_runtime.set_runtime_bridge(_Bridge())
    try:
        result = task_cls({"width": 64}).execute(FlowFile(content=b"in"))
    finally:
        pfp_runtime.set_runtime_bridge(None)

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
            "scope": "conversation",
        })
    host = pfp_runtime.runtime_host_from_invocation(request)

    assert host.user_id == "alice"
    assert host.conversation_id == "conv1"
    assert host.scope == "conversation"
    assert host.caller_runtime["allowed_tools"] == [{"name": "read"}]
    assert host.build_tool_call("read", {})["target"]["name"] == "read"


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


def test_pfp_runtime_host_executes_authorized_service_call(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    from core import pfp_runtime
    from core.pfp_capabilities import PackageCapabilityError

    class _Service:
        def __init__(self):
            self.calls = []

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
        assert "PFP runtime bridge is not implemented yet" in str(exc)
    else:
        raise AssertionError("package runtime proxy should fail closed")


def test_pfp_service_provider_executes_declared_python_subprocess_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True,
        service_runner="python_subprocess")
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
    assert live.invoke("generate", {"prompt": "cat"}) == {
        "operation": "generate",
        "prompt": "cat",
    }


def test_pfp_service_provider_exposes_lifecycle_status_and_operations(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True,
        service_runner="python_subprocess")
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

    from core import FlowFile, TaskError, TaskFactory
    task_cls = TaskFactory.get("packageResizeImage")
    assert task_cls.TYPE == "packageResizeImage"
    assert task_cls.PACKAGE_RUNTIME["object_id"] == "flow_task:resize-image"
    assert task_cls.INSTALLED_FROM["package"] == "community.wavespeed"
    content_dir = Path(task_cls.PACKAGE_RUNTIME["content_dir"])
    assert (content_dir / task_cls.PACKAGE_RUNTIME["entrypoint"]).exists()
    assert task_cls({"width": 64}).get_parameter_schema()["width"]["type"] == "integer"

    from engine.validator import FlowValidator
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64}}},
        "relations": [],
    })
    assert validation.valid is True
    assert not any("not registered" in warning for warning in validation.warnings)

    TaskFactory._tasks.pop("packageResizeImage", None)
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64}}},
        "relations": [],
    })
    assert any("not registered" in warning for warning in validation.warnings)
    reloaded = pfp_package.load_installed_package_tasks(user_id="alice")
    assert reloaded["loaded"][0]["task_type"] == "packageResizeImage"
    validation = FlowValidator().validate({
        "id": "f1",
        "name": "Flow with package task",
        "tasks": {"resize": {"type": "packageResizeImage", "parameters": {"width": 64}}},
        "relations": [],
    })
    assert not any("not registered" in warning for warning in validation.warnings)
    task_cls = TaskFactory.get("packageResizeImage")

    try:
        task_cls({"width": 64}).execute(FlowFile(content=b"img"))
    except TaskError as exc:
        assert "PFP runtime bridge is not implemented yet" in str(exc)
    else:
        raise AssertionError("package flow task proxy should fail closed")

    removed = pfp_package.uninstall_pfp("community.wavespeed", user_id="alice", force=True)
    assert removed["ok"] is True
    assert "packageResizeImage" not in TaskFactory.list_types()
    assert not content_dir.exists()


def test_pfp_flow_task_executes_declared_python_subprocess_runner(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python_subprocess")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "import base64\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = base64.b64decode(payload['flowfile']['content_b64']).decode('utf-8')\n"
        "width = payload['task_config']['width']\n"
        "pfp.result(flowfiles=[pfp.flowfile(f'{content}:{width}', {'resized': width})])\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])

    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile, TaskFactory

    task_cls = TaskFactory.get("packageResizeImage")
    result = task_cls({"width": 64}).execute(FlowFile(content=b"img"))

    assert len(result) == 1
    assert result[0].get_content() == b"img:64"
    assert result[0].attributes == {"resized": "64"}


def test_pfp_flow_task_runs_through_continuous_flow_executor(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_flow_task=True,
        flow_task_runner="python_subprocess")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "import base64\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = base64.b64decode(payload['flowfile']['content_b64']).decode('utf-8')\n"
        "width = payload['task_config']['width']\n"
        "attrs = dict(payload['flowfile'].get('attributes') or {})\n"
        "attrs['resized'] = str(width)\n"
        "pfp.result(flowfiles=[pfp.flowfile(f'{content}:{width}', attrs)])\n",
        encoding="utf-8",
    )
    built = pfp_package.build_pfp(str(pkgdir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        built["path"], user_id="alice", include=["flow_task:resize-image"], force=True)

    from core import FlowFile
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

    flow = FlowParser.parse({
        "id": "pfp-flow-task-e2e",
        "name": "PFP Flow Task E2E",
        "version": "1.0.0",
        "tasks": {
            "resize": {
                "type": "packageResizeImage",
                "parameters": {"width": 96},
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
    )

    assert result.success is True
    assert result.errors == []
    assert len(result.output_flowfiles) == 1
    assert result.output_flowfiles[0].get_content() == b"img:96"
    assert result.output_flowfiles[0].attributes == {"source": "test", "resized": "96"}


def test_pfp_package_installs_and_runs_flow_with_packaged_resources(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, include_service_provider=True, include_flow_task=True,
        flow_task_runner="python_subprocess")
    entrypoint = pkgdir / "content" / "flow-tasks" / "image-resize" / "task.py"
    entrypoint.write_text(
        "import base64\n"
        "from pawflow import pfp\n"
        "payload = pfp.payload\n"
        "content = base64.b64decode(payload['flowfile']['content_b64']).decode('utf-8')\n"
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
                "parameters": {"width": 128},
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

    from core import FlowFile
    from core.repository import ScopedRepository
    from core.service_registry import ServiceRegistry, SCOPE_USER
    from engine.continuous_executor import ContinuousFlowExecutor
    from engine.parser import FlowParser

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
    )

    assert result.success is True
    assert result.errors == []
    assert len(result.output_flowfiles) == 1
    assert result.output_flowfiles[0].get_content() == b"img:128"
    assert result.output_flowfiles[0].attributes == {"flow": "installed"}


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
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill", "tool:reader"], force=True)

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
        package_id="community.base", skill_name="base-skill")
    base = pfp_package.build_pfp(str(base_dir), private_key=keypair["private_key"])
    pfp_package.install_pfp(
        base["path"], user_id="alice", include=["skill:base-skill", "tool:reader"], force=True)

    from core.pfp_capabilities import PackageCapabilityBroker, PackageCapabilityError
    broker = PackageCapabilityBroker(user_id="alice")
    runtime = {
        "package": "community.consumer",
        "object_id": "tool:consumer",
        "allowed_tools": [
            {"name": "read"},
            {"package": "community.base", "version": ">=1.0.0,<2.0.0", "object": "tool:reader"},
        ],
    }

    builtin = broker.authorize_tool_call(runtime, "read")
    assert builtin["target"] == {"kind": "tool", "name": "read", "package": "", "version": ""}

    packaged = broker.authorize_tool_call(runtime, "community.base/tool:reader")
    assert packaged["target"]["package"] == "community.base"
    assert packaged["target"]["name"] == "reader"

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

    resolved = pfp_registry.resolve_package_path(
        "community.wavespeed@1.0.0", user_id="alice")
    assert resolved["downloaded"] is True
    assert resolved["sha256"] == built["sha256"]

    installed = pfp_package.install_pfp(
        resolved["path"], user_id="alice", include=["skill:pkg-skill"], force=True)
    assert installed["ok"] is True


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
                "objects": ["skill:pkg-skill", "agent:helper"],
            },
            {
                "package": "community.wavespeed",
                "version": "1.1.0",
                "description": "WaveSpeed media provider v2",
                "pfp_url": "https://registry.example/community.wavespeed-1.1.0.pfp",
                "sha256": built_v2["sha256"],
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
        "community.wavespeed@1.0.0", user_id="alice")["path"]
    installed = pfp_package.install_pfp(
        installed_path, user_id="alice", include=["skill:pkg-skill", "agent:helper"], force=True)
    assert installed["ok"] is True

    search = pfp_registry.search_registries("provider v2", user_id="alice")
    assert search["results"][0]["ref"] == "community.wavespeed@1.1.0"
    update_path = pfp_registry.resolve_package_path(
        "community.wavespeed@1.1.0", user_id="alice")["path"]
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
    assert store.get("skill", "pkg-skill", "alice")["prompt"] == "Updated registry skill."
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
            },
            {
                "package": "community.wavespeed",
                "version": "1.1.0",
                "pfp_url": "https://registry.example/community.wavespeed-1.1.0.pfp",
                "sha256": built_v2["sha256"],
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
    assert inspected["verified"] is True
    assert inspected["download"]["sha256"] == built_v1["sha256"]

    installed = call("pfp_install", {
        "path": "community.wavespeed@1.0.0",
        "include": ["skill:pkg-skill"],
        "force": True,
    })
    assert installed["ok"] is True

    updated = call("pfp_update", {"path": "community.wavespeed@1.1.0", "force": True})
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
        tmp_path, keypair, tool_runner="python_subprocess",
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
        tmp_path / "v2", keypair, version="1.1.0", tool_runner="python_subprocess",
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

    from core.tool_loader import load_tools_into_registry
    from core.tool_registry import ToolRegistry
    registry = ToolRegistry()
    load_tools_into_registry(registry, "alice")
    handler = registry.get("reader")
    handler.set_user_id("alice")
    assert handler.execute({}) == "v2:sk-v1"


def test_pfp_update_allows_secret_binding_override(tmp_path, monkeypatch):
    _reset_repo(tmp_path, monkeypatch)
    keypair = pfp_package.create_signing_key()
    pkgdir = _write_package_dir(
        tmp_path, keypair, tool_runner="python_subprocess",
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
        tmp_path / "v2", keypair, version="1.1.0", tool_runner="python_subprocess",
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
        "prompt": "Local skill prompt",
        "description": "Local",
    })

    exported = pfp_package.export_pfpdir(
        "alice.local", "0.1.0", ["skill:local-skill"],
        output_dir=str(tmp_path / "exported.pfpdir"), user_id="alice")

    assert exported["ok"] is True
    manifest = json.loads((tmp_path / "exported.pfpdir" / "pfp.json").read_text(encoding="utf-8"))
    assert manifest["package"] == "alice.local"
    assert manifest["objects"][0]["id"] == "skill:local-skill"


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

