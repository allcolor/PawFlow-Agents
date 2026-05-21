from services.tool_relay_service import ToolRelayService


class _Registry:
    def __init__(self, result="ok"):
        self.result = result
        self.executed_args = []

    def get(self, _tool_name):
        return None

    def execute(self, _tool_name, arguments):
        self.executed_args.append(arguments)
        return self.result

    def list_tools(self):
        return []


def _fast_auto_permissions(key, default=None):
    if key == "permission_mode":
        return "auto"
    if key == "tool_permissions":
        return {}
    return default


def test_registry_cache_hit_does_not_list_tools():
    class _ExplodingRegistry:
        def list_tools(self):
            raise AssertionError("cache hit must not enumerate tools")

    ToolRelayService.clear_registry_cache()
    svc = ToolRelayService({"_service_id": "svc1", "file_base_url": ""})
    key = ("svc1", "alice", "conv1", "assistant", "")
    registry = _ExplodingRegistry()
    with ToolRelayService._registry_cache_lock:
        ToolRelayService._registry_cache[key] = registry
        ToolRelayService._registry_cache_tool_counts[key] = 123

    try:
        assert svc._get_registry("alice", "conv1", "assistant") is registry
    finally:
        ToolRelayService.clear_registry_cache()


def test_registry_build_lists_available_filesystems_once(monkeypatch):
    import core.tool_mcp_filters as filters_mod
    import core.tool_registry as registry_mod
    from core.handlers._fs_base import BaseFsHandler

    class _FsHandler(BaseFsHandler):
        name = "read"
        display_name = "Read"
        description = "read"
        parameters_schema = {"type": "object", "properties": {}}

        def execute(self, _args):
            return "ok"

    class _FakeRegistry:
        def __init__(self):
            self.handler = _FsHandler()

        def list_tools(self):
            return [self.handler]

        def unregister(self, _name):
            return None

    calls = []
    available = [{"id": "fs1", "type": "relay", "scope": "user", "root": "/workspace"}]
    svc = ToolRelayService({"_service_id": "svc-once", "file_base_url": ""})
    monkeypatch.setattr(registry_mod, "create_default_registry", _FakeRegistry)
    monkeypatch.setattr(svc, "_load_mcp_tools", lambda *a, **k: None)
    monkeypatch.setattr(filters_mod, "get_filters", lambda _cid: {})
    monkeypatch.setattr(filters_mod, "is_tool_enabled_from_filters", lambda *a, **k: True)

    def _list_once(*_args, **_kwargs):
        calls.append(1)
        return available

    monkeypatch.setattr(svc, "_list_available_filesystem_services", _list_once)
    monkeypatch.setattr(
        svc, "_filesystem_service_from_available",
        lambda avail, *_args: object() if avail else None)
    ToolRelayService.clear_registry_cache()

    try:
        registry = svc._get_registry("alice", "conv1", "assistant")
        assert registry.handler._available_services == available
        assert len(calls) == 1
    finally:
        ToolRelayService.clear_registry_cache()


def test_read_only_search_does_not_resolve_full_env_for_plain_args(monkeypatch):
    import services.tool_relay_service as relay_mod

    ToolRelayService.clear_runtime_caches()
    registry = _Registry("plain TOPSECRET output")
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: False))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(
        relay_mod, "resolve_secrets_env",
        lambda *_args: (_ for _ in ()).throw(AssertionError("env should stay lazy")))
    secret_calls = []
    fingerprint_calls = []

    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv: fingerprint_calls.append((uid, conv)) or ("fp",)))

    def _secret_values(*_args):
        secret_calls.append(1)
        return {"TOPSECRET"}, {"TOPSECRET": "TOKEN"}

    monkeypatch.setattr(relay_mod, "resolve_secret_values", _secret_values)

    first = svc._do_execute("r1", "search", {"path": "tests", "pattern": "needle"},
                            "alice", "conv1", "assistant")
    second = svc._do_execute("r2", "search", {"path": "tests", "pattern": "needle"},
                             "alice", "conv1", "assistant")

    assert "TOPSECRET" not in first["data"]
    assert "TOPSECRET" not in second["data"]
    assert "Redacted" in first["data"]
    assert len(secret_calls) == 1
    assert len(fingerprint_calls) == 1


def test_bash_still_receives_secret_environment(monkeypatch):
    import services.tool_relay_service as relay_mod
    from core.tool_approval import ToolApprovalGate

    ToolRelayService.clear_runtime_caches()
    registry = _Registry("ok")
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: False))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(ToolApprovalGate, "_is_catastrophic_command", lambda _cmd: False)
    env_calls = []
    fingerprint_calls = []

    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv: fingerprint_calls.append((uid, conv)) or ("fp",)))

    def _env(*_args):
        env_calls.append(1)
        return {"TOKEN": "TOPSECRET"}

    monkeypatch.setattr(relay_mod, "resolve_secrets_env", _env)
    monkeypatch.setattr(relay_mod, "resolve_secret_values", lambda *_args: (set(), {}))

    result = svc._do_execute("r1", "bash", {"command": "echo $TOKEN"},
                             "alice", "conv1", "assistant")
    second = svc._do_execute("r2", "bash", {"command": "echo $TOKEN"},
                             "alice", "conv1", "assistant")

    assert result["data"] == "ok"
    assert second["data"] == "ok"
    assert registry.executed_args[0]["_secret_env"] == {"TOKEN": "TOPSECRET"}
    assert registry.executed_args[0]["command"] == "echo $TOKEN"
    assert len(env_calls) == 1
    assert len(fingerprint_calls) == 2
