from types import SimpleNamespace

import pytest

from services.filesystem_service import RelayService
from services.tool_relay_service import ToolRelayService
import services._tool_relay_base as _trb_mod


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


def _fast_auto_permissions(*args, **_kwargs):
    if len(args) >= 3:
        key = args[1]
        default = args[2]
    else:
        key = args[0] if args else ""
        default = args[1] if len(args) > 1 else None
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


def test_relay_connection_change_clears_tool_registry_cache():
    key = ("tool-relay", "alice", "conv1", "assistant", "")
    ToolRelayService.clear_registry_cache()
    relay = RelayService({"_service_id": "fs1"})

    try:
        with ToolRelayService._registry_cache_lock:
            ToolRelayService._registry_cache[key] = object()
            ToolRelayService._registry_cache_tool_counts[key] = 1

        relay._set_relay(object(), object(), object(), object())
        assert ToolRelayService._registry_cache == {}
        assert ToolRelayService._registry_cache_tool_counts == {}

        with ToolRelayService._registry_cache_lock:
            ToolRelayService._registry_cache[key] = object()
            ToolRelayService._registry_cache_tool_counts[key] = 1

        relay._clear_relay()
        assert ToolRelayService._registry_cache == {}
        assert ToolRelayService._registry_cache_tool_counts == {}
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


def test_registry_build_applies_llm_service_tool_result_limit(monkeypatch):
    import core.conv_agent_config as agent_config_mod
    import core.service_registry as service_registry_mod
    import core.tool_mcp_filters as filters_mod
    import core.tool_registry as registry_mod

    class _LimitedHandler:
        name = "read"
        _tool_result_max_chars = 50000

    class _FakeRegistry:
        def __init__(self):
            self.handler = _LimitedHandler()

        def list_tools(self):
            return [self.handler]

        def unregister(self, _name):
            return None

    class _ServiceRegistry:
        def __init__(self):
            self.calls = []

        def resolve_definition(self, service_id, *, user_id="", conv_id=""):
            self.calls.append((service_id, user_id, conv_id))
            return SimpleNamespace(config={"tool_result_max_chars": "3500"})

    fake_services = _ServiceRegistry()
    svc = ToolRelayService({"_service_id": "svc-limit", "file_base_url": ""})
    monkeypatch.setattr(registry_mod, "create_default_registry", _FakeRegistry)
    monkeypatch.setattr(svc, "_load_mcp_tools", lambda *a, **k: None)
    monkeypatch.setattr(filters_mod, "get_filters", lambda _cid: {})
    monkeypatch.setattr(filters_mod, "is_tool_enabled_from_filters", lambda *a, **k: True)
    monkeypatch.setattr(svc, "_list_available_filesystem_services", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_filesystem_service_from_available", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_config_mod, "get_agent_config",
        lambda conv_id, agent_name: {"llm_service": "agy_llm"})
    monkeypatch.setattr(
        service_registry_mod.ServiceRegistry, "get_instance",
        classmethod(lambda cls: fake_services))

    ToolRelayService.clear_registry_cache()
    try:
        registry = svc._get_registry("alice", "conv1", "assistant")
        assert registry.handler._tool_result_max_chars == 3500
        assert fake_services.calls == [("agy_llm", "alice", "conv1")]
    finally:
        ToolRelayService.clear_registry_cache()


def test_tool_relay_injects_source_context_for_flash_delegate(monkeypatch):
    import core.conv_agent_config as agent_config_mod
    from core.handlers.resource_agent import FlashAgentHandler

    class _FlashProbe(FlashAgentHandler):
        def execute(self, _arguments):
            src_agent = getattr(self._local, "source_agent", "") or ""
            src_svc = getattr(self._local, "source_llm_service", "") or ""
            delegate_tc_id = getattr(self._local, "delegate_tc_id", "") or ""
            return f"src={src_agent};svc={src_svc};tc={delegate_tc_id}"

    class _FlashRegistry:
        def __init__(self):
            self.handler = _FlashProbe()

        def get(self, name):
            return self.handler if name == "flash_delegate" else None

        def execute(self, name, arguments):
            return self.get(name).execute(arguments)

        def list_tools(self):
            return [self.handler]

    svc = ToolRelayService({"_service_id": "svc-flash", "file_base_url": ""})
    registry = _FlashRegistry()
    monkeypatch.setattr(svc, "_get_registry", lambda *_args, **_kwargs: registry)
    monkeypatch.setattr(svc, "_conversation_extra_fast", _fast_auto_permissions)
    monkeypatch.setattr(svc, "_conversation_has_hooks", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        agent_config_mod, "get_agent_config",
        lambda conv_id, agent_name: {"llm_service": "svc_a"},
    )

    result = svc._do_execute(
        "tc_flash", "flash_delegate", {"tasks": []},
        "alice", "conv1", "agentA",
    )

    assert result["data"] == "src=agentA;svc=svc_a;tc=tc_flash"


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
        _trb_mod, "resolve_secrets_env",
        lambda *_args: (_ for _ in ()).throw(AssertionError("env should stay lazy")))
    secret_calls = []
    fingerprint_calls = []

    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv: fingerprint_calls.append((uid, conv)) or ("fp",)))

    def _secret_values(*_args):
        secret_calls.append(1)
        return {"TOPSECRET"}, {"TOPSECRET": "TOKEN"}

    monkeypatch.setattr(_trb_mod, "resolve_secret_values", _secret_values)

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

    monkeypatch.setattr(_trb_mod, "resolve_secrets_env", _env)
    monkeypatch.setattr(_trb_mod, "resolve_secret_values", lambda *_args: (set(), {}))

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


def test_subconversation_tool_execution_uses_parent_runtime_scope(monkeypatch):
    import services.tool_relay_service as relay_mod

    ToolRelayService.clear_runtime_caches()
    registry = _Registry("ok")
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    hook_cids = []
    extra_cids = []
    env_cids = []
    secret_cids = []
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks",
        classmethod(lambda cls, cid, uid: hook_cids.append(cid) or False))

    def _extra(cid, key, default=None):
        extra_cids.append((cid, key))
        return _fast_auto_permissions(key, default)

    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast", staticmethod(_extra))
    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv: ("fp", conv)))
    monkeypatch.setattr(
        _trb_mod, "resolve_secrets_env",
        lambda uid, conv: env_cids.append(conv) or {"TOKEN": "secret"})
    monkeypatch.setattr(
        _trb_mod, "resolve_secret_values",
        lambda uid, conv: secret_cids.append(conv) or (set(), {}))

    for cid in ("conv1::task_verify::t_1", "conv1::delegate::assistant"):
        assert svc._do_execute("rid", "bash", {"command": "echo $TOKEN"},
                               "alice", cid, "assistant")["data"] == "ok"

    assert hook_cids == ["conv1", "conv1"]
    assert {cid for cid, key in extra_cids if key == "permission_mode"} == {"conv1"}
    assert env_cids == ["conv1"]
    assert secret_cids == ["conv1"]


def test_handle_execute_retries_relay_transport_errors(monkeypatch):
    import services.tool_relay_service as relay_mod

    svc = ToolRelayService({})
    calls = {"count": 0}
    sleeps = []

    def _execute(request_id, tool_name, arguments, user_id, conversation_id, agent_name):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Relay not connected")
        return {"type": "result", "request_id": request_id, "data": "ok"}

    monkeypatch.setattr(svc, "_do_execute", _execute)
    monkeypatch.setattr(relay_mod.time, "sleep", lambda delay: sleeps.append(delay))

    result = svc._handle_execute(
        "rid-retry", "read", {"path": "README.md"}, "alice", "conv1", "assistant")

    assert result["data"] == "ok"
    assert calls["count"] == 2
    assert [delay for delay in sleeps if delay == 5.0] == [5.0]


def test_do_execute_reraises_relay_transport_errors(monkeypatch):
    class _DisconnectRegistry(_Registry):
        def execute(self, _tool_name, _arguments):
            raise RuntimeError("Relay disconnected")

    registry = _DisconnectRegistry()
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: False))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(
        svc, "_cached_secret_values", lambda *_args: (set(), {}))

    with pytest.raises(RuntimeError, match="Relay disconnected"):
        svc._do_execute("rid", "read", {"path": "README.md"},
                        "alice", "conv1", "assistant")


def test_handle_execute_retries_relay_transport_error_results(monkeypatch):
    import services.tool_relay_service as relay_mod

    registry = _Registry()
    registry.results = iter([
        "Error reading 'README.md': Relay disconnected",
        "ok",
    ])

    def _execute(_tool_name, arguments):
        registry.executed_args.append(arguments)
        return next(registry.results)

    registry.execute = _execute
    svc = ToolRelayService({})
    sleeps = []
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: False))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(svc, "_cached_secret_values", lambda *_args: (set(), {}))
    monkeypatch.setattr(relay_mod.time, "sleep", lambda delay: sleeps.append(delay))

    result = svc._handle_execute(
        "rid-result-retry", "read", {"path": "README.md"},
        "alice", "conv1", "assistant")

    assert result["data"] == "ok"
    assert len(registry.executed_args) == 2
    assert [delay for delay in sleeps if delay == 5.0] == [5.0]


def test_handle_execute_does_not_retry_exhausted_relay_results(monkeypatch):
    import services.tool_relay_service as relay_mod

    exhausted = (
        "Error reading 'README.md': Relay transport retry attempts exhausted "
        "for read_file: Relay disconnected"
    )
    registry = _Registry(exhausted)
    svc = ToolRelayService({})
    sleeps = []

    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: False))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(svc, "_cached_secret_values", lambda *_args: (set(), {}))
    monkeypatch.setattr(relay_mod.time, "sleep", lambda delay: sleeps.append(delay))

    result = svc._handle_execute(
        "rid-exhausted", "read", {"path": "README.md"},
        "alice", "conv1", "assistant")

    assert result["data"] == exhausted
    assert [delay for delay in sleeps if delay == 5.0] == []
