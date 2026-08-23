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
        classmethod(lambda cls, uid, conv, agent_name="":
                    fingerprint_calls.append((uid, conv, agent_name)) or ("fp",)))

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
    # One fingerprint per execution: it is the cache's staleness check, so
    # it runs on hits too (a secret added mid-conversation must be picked
    # up and redacted on the very next call). Resolution stays cached.
    assert len(fingerprint_calls) == 2


def test_bash_still_receives_secret_environment(monkeypatch):
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
        classmethod(lambda cls, uid, conv, agent_name="":
                    fingerprint_calls.append((uid, conv, agent_name)) or ("fp",)))

    def _env(*_args):
        env_calls.append(1)
        return {"TOKEN": "TOPSECRET"}

    monkeypatch.setattr(_trb_mod, "resolve_secrets_env", _env)
    monkeypatch.setattr(_trb_mod, "resolve_secret_values", lambda *_args: (set(), {}))

    public_args = {
        "command": "echo $TOKEN",
        "metadata": {"reference": "$TOKEN"},
    }
    result = svc._do_execute("r1", "bash", public_args,
                             "alice", "conv1", "assistant")
    second = svc._do_execute("r2", "bash", {"command": "echo $TOKEN"},
                             "alice", "conv1", "assistant")

    assert result["data"] == "ok"
    assert second["data"] == "ok"
    assert registry.executed_args[0]["_secret_env"] == {"TOKEN": "TOPSECRET"}
    assert registry.executed_args[0]["command"] == "echo $TOKEN"
    assert registry.executed_args[0]["metadata"] == {"reference": "TOPSECRET"}
    assert public_args == {
        "command": "echo $TOKEN",
        "metadata": {"reference": "$TOKEN"},
    }
    assert len(env_calls) == 1
    assert len(fingerprint_calls) == 2


def test_post_tool_hook_never_receives_private_secret_environment(monkeypatch):
    import core.agent_hooks as hooks_mod
    from core.tool_approval import ToolApprovalGate

    ToolRelayService.clear_runtime_caches()
    registry = _Registry("ok")
    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_get_registry", lambda *args: registry)
    monkeypatch.setattr(
        ToolRelayService, "_conversation_has_hooks", classmethod(lambda *args: True))
    monkeypatch.setattr(
        ToolRelayService, "_conversation_extra_fast",
        staticmethod(lambda _cid, key, default=None: _fast_auto_permissions(key, default)))
    monkeypatch.setattr(ToolApprovalGate, "_is_catastrophic_command", lambda _cmd: False)
    monkeypatch.setattr(
        ToolRelayService, "_secret_config_fingerprint",
        classmethod(lambda cls, uid, conv, agent_name="": ("fp",)))
    monkeypatch.setattr(
        _trb_mod, "resolve_secrets_env", lambda *_args: {"TOKEN": "CANARY_SECRET"})
    monkeypatch.setattr(
        _trb_mod, "resolve_secret_values", lambda *_args: (set(), {}))
    events = []

    class _HookRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, event, payload, **_kwargs):
            events.append((event, payload))
            return {"decision": "allow"}

    monkeypatch.setattr(hooks_mod, "AgentHookRunner", _HookRunner)
    original = {"command": "printf %s $TOKEN", "nested": {"ref": "$TOKEN"}}

    result = svc._do_execute(
        "r1", "bash", original, "alice", "conv1", "assistant")

    assert result["data"] == "ok"
    assert registry.executed_args[0]["_secret_env"] == {
        "TOKEN": "CANARY_SECRET"}
    assert registry.executed_args[0]["nested"] == {"ref": "CANARY_SECRET"}
    assert original == {"command": "printf %s $TOKEN", "nested": {"ref": "$TOKEN"}}
    post_payload = next(payload for event, payload in events
                        if event == "post_tool_call")
    assert post_payload["arguments"] == original
    assert "_secret_env" not in post_payload["arguments"]


def test_subconversation_tool_execution_uses_parent_runtime_scope(monkeypatch):

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
        classmethod(lambda cls, uid, conv, agent_name="":
                    ("fp", conv, agent_name)))
    monkeypatch.setattr(
        _trb_mod, "resolve_secrets_env",
        lambda uid, conv, agent_name="":
        env_cids.append(conv) or {"TOKEN": "secret"})
    monkeypatch.setattr(
        _trb_mod, "resolve_secret_values",
        lambda uid, conv, agent_name="":
        secret_cids.append(conv) or (set(), {}))

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


def test_flash_delegate_derives_source_context_from_agent_name(monkeypatch):
    """use_tool calls bypass _do_execute injection; the handler must derive
    the calling agent identity from the registry-wired agent name and the
    conversation agent config's llm_service."""
    import core.conv_agent_config as agent_config_mod
    from core.handlers.flash_agent import FlashAgentHandler

    class _Probe(FlashAgentHandler):
        def execute(self, _arguments):
            agent, svc = self._resolve_source_context()
            return f"src={agent};svc={svc}"

    monkeypatch.setattr(
        agent_config_mod, "get_agent_config",
        lambda conv_id, agent_name: {"llm_service": "svc_a"})

    handler = _Probe()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_agent_name("agentA")

    assert handler.execute({}) == "src=agentA;svc=svc_a"


def test_use_tool_flash_delegate_receives_derived_source_context(monkeypatch):
    """End-to-end through the meta-tool: an API agent calling
    use_tool(tool_name='flash_delegate') must not hit the BUG guard."""
    import core.conv_agent_config as agent_config_mod
    from core.handlers.flash_agent import FlashAgentHandler
    from core.handlers.meta_tools import UseToolHandler
    from core.tool_registry import ToolRegistry

    class _Probe(FlashAgentHandler):
        def execute(self, _arguments):
            agent, svc = self._resolve_source_context()
            return f"src={agent};svc={svc}"

    monkeypatch.setattr(
        agent_config_mod, "get_agent_config",
        lambda conv_id, agent_name: {"llm_service": "svc_a"})

    reg = ToolRegistry()
    handler = _Probe()
    handler.set_spawn_deps(None, lambda svc, uid: (None, None), None, registry=reg)
    reg.register(handler)
    # Registry wiring (services/_tool_relay_registry.py) sets these on every
    # handler before any tool executes.
    handler.set_user_id("alice")
    handler.set_conversation_id("conv1")
    handler.set_agent_name("agentA")

    result = UseToolHandler(reg).execute({
        "tool_name": "flash_delegate",
        "arguments": {"tasks": []},
    })
    assert result == "src=agentA;svc=svc_a"


def test_flash_delegate_missing_context_returns_clear_error():
    """With neither a thread-local source nor a wired agent name, the guard
    must fail with an actionable message, not a bare BUG string."""
    from core.handlers.flash_agent import FlashAgentHandler

    handler = FlashAgentHandler()
    handler.set_spawn_deps(None, lambda svc, uid: (None, None), None)
    result = handler.execute({
        "tasks": [{"name": "x", "prompt": "p", "message": "m"}]
    })
    assert result.startswith("Error: flash_delegate could not determine")


def test_flash_delegate_explicit_source_agent_wins(monkeypatch):
    """A populated thread-local context (the _do_execute injection) stays
    authoritative; get_agent_config must not even be consulted."""
    import core.conv_agent_config as agent_config_mod
    from core.handlers.flash_agent import FlashAgentHandler

    class _Probe(FlashAgentHandler):
        def execute(self, _arguments):
            agent, svc = self._resolve_source_context()
            return f"src={agent};svc={svc}"

    def _raise(*_a, **_k):
        raise AssertionError("get_agent_config must not be called")

    monkeypatch.setattr(agent_config_mod, "get_agent_config", _raise)

    handler = _Probe()
    handler.set_agent_name("otherAgent")
    handler.set_source_agent("agentA", "svc_x")

    assert handler.execute({}) == "src=agentA;svc=svc_x"
