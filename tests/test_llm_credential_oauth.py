import json
from pathlib import Path
from types import SimpleNamespace
import inspect
import pytest

from services.llm_connection import LLMConnectionService
from services.llm_credential_oauth import (
    LLMCredentialOAuthProviderService,
    credential_pool_allows_refresh,
    credential_service_id_from_llm_service,
    normalize_provider,
    resolve_credential_service_id,
)
from core.service_registry import ServiceRegistry
from core.llm_providers import claude_code_session, codex_session, gemini_session
from core.llm_client import LLMClient


def _sdef(service_id, service_type, config):
    return SimpleNamespace(service_id=service_id, service_type=service_type, config=config)


def test_llm_service_references_external_credential_provider():
    schema = LLMConnectionService({}).get_parameter_schema()
    assert "experimental" not in schema
    assert schema["credential_service_id"]["type"] == "service_ref"
    assert schema["credential_service_id"]["service_type"] == "llmCredentialOAuthProvider"
    assert schema["credential_service_id"]["provider_aliases"] == {
        "claude-code-interactive": "claude-code",
        "antigravity-interactive": "gemini",
        "codex-interactive": "codex-app-server",
        # Managed MCP providers reuse the same three OAuth pools.
        "cc_mcp": "claude-code",
        "codex_mcp": "codex-app-server",
        "agy_mcp": "gemini",
    }
    # CLI providers own no login action here — they reference a credential
    # pool. Copilot is not a pool: its device flow just fills api_key.
    assert [a["id"] for a in LLMConnectionService({}).get_service_actions()] == [
        "omniroute_models_list", "copilot_device_login"]


def test_credential_provider_exposes_login_and_pool_actions():
    actions = LLMCredentialOAuthProviderService({"provider": "codex-app-server"}).get_service_actions()
    ids = {a["id"] for a in actions}
    assert "credential_pool_manage" in ids
    assert "codex_login" in ids
    assert "gemini_login" in ids
    assert "agy_server_login" in ids
    assert "claude_code_login" in ids
    manage = next(a for a in actions if a["id"] == "credential_pool_manage")
    assert manage["flow"] == "credential_table"
    assert manage["server_action"] == "llm_credential_pool_list"


def test_credential_refresh_policy_uses_declarative_provider_defaults(
        monkeypatch):
    service = LLMCredentialOAuthProviderService({})
    schema = service.get_parameter_schema()
    assert schema["allow_refresh"]["type"] == "boolean"
    assert schema["allow_refresh"]["default"] is True
    assert service.resolve_parameter_value(
        "allow_refresh", {"provider": "claude-code"}) is True
    assert service.resolve_parameter_value(
        "allow_refresh", {"provider": "codex-app-server"}) is True
    assert service.resolve_parameter_value(
        "allow_refresh", {"provider": "generic"}) is True
    assert service.resolve_parameter_value(
        "allow_refresh", {"provider": "gemini"}) is False
    gemini_schema = service.resolve_parameter_schema({"provider": "gemini"})
    assert "Antigravity account warning" in gemini_schema["allow_refresh"]["notice"]
    assert service.resolve_parameter_value(
        "allow_refresh", {
            "provider": "gemini", "allow_refresh": True,
        }) is True

    assert credential_pool_allows_refresh(config={}) is True
    assert credential_pool_allows_refresh(
        config={"provider": "gemini"}) is False
    assert credential_pool_allows_refresh(config={
        "provider": "gemini", "allow_refresh": True,
    }) is True
    assert credential_pool_allows_refresh(config={
        "provider": "claude-code", "allow_refresh": False,
    }) is False

    llm = _sdef("llm", "llmConnection", {
        "provider": "gemini", "credential_service_id": "pool"})
    pool = _sdef("pool", "llmCredentialOAuthProvider", {
        "provider": "gemini"})
    by_id = {"llm": llm, "pool": pool}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id))

    assert credential_pool_allows_refresh(
        "llm", user_id="alice", conv_id="conv-1") is False


def test_generic_expired_token_is_returned_without_refresh_when_disabled(
        monkeypatch):
    from core import llm_oauth_credential

    monkeypatch.setattr(llm_oauth_credential, "load_pool", lambda _sid: [{
        "access_token": "expired-access",
        "refresh_token": "native-client-refresh",
        "expires_at": 1,
    }])
    calls = []
    monkeypatch.setattr(
        llm_oauth_credential, "_refresh",
        lambda *_args, **_kwargs: calls.append(True))

    token = llm_oauth_credential.access_token(
        "pool", {"allow_refresh": False}, {"token_url": "https://id/token"})

    assert token == "expired-access"
    assert calls == []


@pytest.mark.parametrize(
    "provider,module,setup_name,force_name,credential,relative_path,token_path",
    [
        ("claude-code", claude_code_session, "_setup_credentials",
         "_force_refresh_pool_entry",
         {"access_token": "expired-access", "refresh_token": "native-refresh",
          "expires_at": 1},
         ".credentials.json", ("claudeAiOauth", "accessToken")),
        ("codex-app-server", codex_session, "_codex_setup_credentials",
         "_codex_force_refresh_pool_entry",
         {"access_token": "expired-access", "refresh_token": "native-refresh",
          "id_token": "identity", "expires_at": 1},
         ".codex/auth.json", ("tokens", "access_token")),
        ("gemini", gemini_session, "_gemini_setup_credentials",
         "_gemini_force_refresh_pool_entry",
         {"access_token": "expired-access", "refresh_token": "native-refresh",
          "expires_at": 1},
         ".gemini/oauth_creds.json", ("access_token",)),
    ],
)
def test_disabled_cli_pool_delegates_refresh_to_native_client(
        monkeypatch, tmp_path, provider, module, setup_name, force_name,
        credential, relative_path, token_path):
    monkeypatch.setattr(
        "services.llm_credential_oauth.credential_pool_allows_refresh",
        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module, "_load_credentials_pool",
        lambda *_args, **_kwargs: [dict(credential)])
    network_calls = []
    if provider == "claude-code":
        network_target = "_refresh_oauth_token"
    else:
        network_target = "refresh_oauth_token"
        monkeypatch.setattr(
            module, network_target,
            lambda *_args, **_kwargs: network_calls.append(True))

    client = LLMClient(provider=provider, config={})
    client._agent_service = "pool"
    if provider == "claude-code":
        monkeypatch.setattr(
            client, network_target,
            lambda *_args, **_kwargs: network_calls.append(True))

    getattr(client, setup_name)(
        str(tmp_path), pool_index=0, user_id="alice", conversation_id="conv-1")
    blob = json.loads((tmp_path / relative_path).read_text(encoding="utf-8"))
    for key in token_path:
        blob = blob[key]

    assert blob == "expired-access"
    assert getattr(client, force_name)(
        0, user_id="alice", conversation_id="conv-1") is False
    assert network_calls == []


def test_credential_pool_actions_expose_and_enforce_disabled_refresh(
        monkeypatch):
    from core import FlowFile
    from tasks.ai.actions import _sf_k1, _sf_k2

    class _PoolModule:
        @staticmethod
        def _load_credentials_pool(*_args, **_kwargs):
            return []

    monkeypatch.setattr(
        _sf_k1, "_credential_provider_for_service", lambda *_args: "gemini")
    monkeypatch.setattr(_sf_k1, "_credential_module", lambda _provider: _PoolModule)
    monkeypatch.setattr(
        _sf_k1, "credential_pool_allows_refresh",
        lambda *_args, **_kwargs: False)
    listed = FlowFile(content=b"")
    list_result = _sf_k1._handle_sf_k1(
        None, "llm_credential_pool_list", {"service_id": "pool"}, None,
        "alice", listed, (None,) * 6)
    assert json.loads(list_result[0].get_content())["allow_refresh"] is False

    monkeypatch.setattr(
        _sf_k2, "_credential_provider_for_service", lambda *_args: "gemini")
    monkeypatch.setattr(
        _sf_k2, "credential_pool_allows_refresh",
        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        _sf_k2, "_credential_module",
        lambda _provider: pytest.fail("refresh module must not be loaded"))
    refreshed = FlowFile(content=b"")
    refresh_result = _sf_k2._handle_sf_k2(
        None, "llm_credential_pool_refresh",
        {"service_id": "pool", "index": 0}, None, "alice", refreshed,
        (None,) * 6)
    error = json.loads(refresh_result[0].get_content())["error"]
    assert "disabled" in error


def test_credential_pool_resolution_prefers_llm_reference(monkeypatch):
    llm = _sdef(
        "codex_appserver_llm_service",
        "llmConnection",
        {"provider": "codex-app-server", "credential_service_id": "codex_oauth_credentials"},
    )
    cred = _sdef(
        "codex_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "codex-app-server"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert credential_service_id_from_llm_service("codex-app-server", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("codex-app-server", cred.service_id) == cred.service_id


def test_cli_pool_helpers_resolve_user_scoped_credential_services(monkeypatch):
    services = {}
    cases = [
        (
            claude_code_session._find_cc_service_id,
            "claude-code",
            "claude_code_llm_service",
            "claude_code_oauth_credentials",
        ),
        (
            codex_session._find_codex_service_id,
            "codex-app-server",
            "codex_appserver_llm_service",
            "codex_oauth_credentials",
        ),
        (
            gemini_session._find_gemini_service_id,
            "gemini",
            "gemini_llm_service",
            "gemini_oauth_credentials",
        ),
    ]
    for _, provider, llm_id, cred_id in cases:
        services[llm_id] = _sdef(
            llm_id,
            "llmConnection",
            {"provider": provider, "credential_service_id": cred_id},
        )
        services[cred_id] = _sdef(
            cred_id,
            "llmCredentialOAuthProvider",
            {"provider": provider},
        )

    def scoped_get_service_def(service_id, user_id="", conv_id=""):
        if user_id == "alice" and conv_id == "conv-1":
            return services.get(service_id)
        return None

    def scoped_all_service_defs(user_id="", conv_id=""):
        if user_id == "alice" and conv_id == "conv-1":
            return list(services.values())
        return []

    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        scoped_get_service_def,
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        scoped_all_service_defs,
    )

    for finder, _, llm_id, cred_id in cases:
        assert finder(llm_id, user_id="alice", conv_id="conv-1") == cred_id
        assert finder(llm_id) == ""


def test_cli_runtime_token_resolution_passes_scope_to_pool_loaders(monkeypatch):
    calls = []
    cases = [
        (claude_code_session, claude_code_session.ClaudeCodeSessionMixin, "_resolve_service_tokens"),
        (codex_session, codex_session.CodexSessionMixin, "_codex_resolve_service_tokens"),
        (gemini_session, gemini_session.GeminiSessionMixin, "_gemini_resolve_service_tokens"),
    ]

    for module, mixin, method_name in cases:
        def fake_load(service_id="", user_id="", conv_id="", module_name=module.__name__):
            calls.append((module_name, service_id, user_id, conv_id))
            return []

        monkeypatch.setattr(module, "_load_credentials_pool", fake_load)
        client = mixin()
        client._agent_service = "scoped_llm_service"
        client._user_id = "alice"
        client._conversation_id = "conv-1"
        getattr(client, method_name)()

    assert calls == [
        (claude_code_session.__name__, "scoped_llm_service", "alice", "conv-1"),
        (codex_session.__name__, "scoped_llm_service", "alice", "conv-1"),
        (gemini_session.__name__, "scoped_llm_service", "alice", "conv-1"),
    ]


def test_agent_llm_resolver_annotates_cli_client_with_service_scope(monkeypatch):
    from tasks.ai.agent_utils import AgentUtilsMixin

    class _Svc:
        config = {}

        def get_client(self, pool_index=-1):
            return LLMClient(provider="codex-app-server", config={})

    calls = []

    class _Reg:
        def resolve(self, service_id, user_id="", conv_id=""):
            calls.append((service_id, user_id, conv_id))
            return _Svc()

    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Reg()),
    )
    holder = AgentUtilsMixin()
    holder._services = {}

    client, svc = holder._resolve_llm_service(
        "codex_llm", "alice", "conv-1::task::task-a")

    assert svc is not None
    assert calls == [("codex_llm", "alice", "conv-1::task::task-a")]
    assert client._agent_service == "codex_llm"
    assert client._user_id == "alice"
    assert client._conversation_id == "conv-1::task::task-a"


def test_delegate_bootstrap_resolver_keeps_conversation_scope():
    src = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
    assert "_resolve_llm_service(svc_id, uid, st.conversation_id)" in src


def test_flash_subconversation_resolves_parent_scope():
    from core.service_registry import _parent_conversation_id

    assert _parent_conversation_id("conv-1::flash::audit") == "conv-1"
    assert _parent_conversation_id("conv-1::task::t1::flash::audit") == "conv-1"


def test_credential_pool_actions_pass_user_scope_to_cli_helpers():
    src = "".join(
    Path(f"tasks/ai/actions/{_sf}").read_text(encoding="utf-8")
    for _sf in (
        "service_flow.py",
        "_sf_base.py",
        "_sf_routes.py",
        "_sf_k1.py",
        "_sf_k2.py",
        "_sf_k3.py",
        "_sf_k4.py",
        "_sf_k5.py",
        "_sf_k6.py",
        "_sf_k7.py",
        "_sf_k8.py",
        "_sf_k9.py"))

    assert "mod._load_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)" in src
    assert "mod.reset_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)" in src
    assert "mod.remove_credential_from_pool(idx, svc_id, user_id=user_id, conv_id=conv_id)" in src
    assert "_load_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)" in src
    assert "service_id=service_id, user_id=user_id, conv_id=conv_id" in src
    assert "id_token=id_token, user_id=user_id" in src
    assert "conv_id=conv_id" in src


def test_add_credential_refuses_unresolved_service(monkeypatch):
    cases = [
        (claude_code_session, "_find_cc_service_id"),
        (codex_session, "_find_codex_service_id"),
        (gemini_session, "_find_gemini_service_id"),
    ]

    for module, finder_name in cases:
        monkeypatch.setattr(module, finder_name, lambda *args, **kwargs: "")
        with pytest.raises(ValueError):
            module.add_credential_to_pool(
                "access", "refresh", 1234567890,
                service_id="missing_service", user_id="alice", conv_id="conv-1")


def test_claude_code_interactive_reuses_claude_code_credentials(monkeypatch):
    assert normalize_provider("claude-code-interactive") == "claude-code"
    llm = _sdef(
        "claude_code_interactive_llm_service",
        "llmConnection",
        {"provider": "claude-code-interactive", "credential_service_id": "claude_code_oauth_credentials"},
    )
    cred = _sdef(
        "claude_code_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "claude-code"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert credential_service_id_from_llm_service("claude-code-interactive", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("claude-code-interactive", cred.service_id) == cred.service_id


def test_antigravity_interactive_reuses_gemini_credentials(monkeypatch):
    assert normalize_provider("antigravity-interactive") == "gemini"
    llm = _sdef(
        "antigravity_interactive_llm_service",
        "llmConnection",
        {"provider": "antigravity-interactive", "credential_service_id": "gemini_oauth_credentials"},
    )
    cred = _sdef(
        "gemini_oauth_credentials",
        "llmCredentialOAuthProvider",
        {"provider": "gemini"},
    )
    by_id = {llm.service_id: llm, cred.service_id: cred}
    monkeypatch.setattr(
        "services.llm_credential_oauth.get_service_def",
        lambda service_id, user_id="", conv_id="": by_id.get(service_id),
    )
    monkeypatch.setattr(
        "services.llm_credential_oauth._all_service_defs",
        lambda user_id="", conv_id="": list(by_id.values()),
    )

    assert credential_service_id_from_llm_service("antigravity-interactive", llm.service_id) == cred.service_id
    assert resolve_credential_service_id("antigravity-interactive", cred.service_id) == cred.service_id


def test_service_registry_does_not_rewrite_credential_services_on_load():
    assert "_migrate_llm_oauth_credentials" not in inspect.getsource(ServiceRegistry._ensure_loaded)
    assert "_migrate_llm_oauth_credentials" not in inspect.getsource(ServiceRegistry.reload_scope)


def test_credential_dialog_filters_provider_actions_without_parameter_rules():
    src = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))
    src += Path("tasks/io/chat_ui/schema_form.js").read_text(encoding="utf-8")
    assert "rules = rules || [];" in src
    assert "actions = actions || [];" in src
    assert "if (!rules || !rules.length) return;" not in src


def test_service_ref_ui_supports_provider_aliases():
    src = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))
    src += Path("tasks/io/chat_ui/schema_form.js").read_text(encoding="utf-8")
    assert "data-provider-aliases" in src
    assert "function _serviceRefProviderMatches" in src
    assert "s.provider === wantedProvider" not in src


def test_credential_pool_ui_hides_manual_refresh_when_disabled():
    src = Path(
        "tasks/io/chat_ui/resources_service_login.js").read_text(
            encoding="utf-8")

    assert "const allowRefresh = resp.allow_refresh !== false;" in src
    assert "(allowRefresh ? '<button type=\"button\" data-cred-refresh=" in src


def test_schema_form_applies_boolean_rule_defaults_without_overwriting_values():
    src = Path("tasks/io/chat_ui/schema_form.js").read_text(encoding="utf-8")

    assert "data-rule-default-owned" in src
    assert "wrapper.dataset.ruleDefaultOwned !== '0'" in src
    assert "input.type === 'checkbox'" in src
    assert "input.checked = effects.default === true" in src
    assert "if (effects.notice)" in src
    assert "provider === 'gemini'" not in src
